import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from ..db import get_db
from ..mc_launcher import (
    ExplorationStopped,
    clear_exploration_state,
    ensure_server_running,
    flush_exploration_state,
    pause_exploration,
    reset_controller,
    start_exploration,
    stop_exploration,
    stop_other_explorations,
    switch_world,
)
from ..models import MissionCreate, MissionOut, MissionPatch
from ..security import get_current_user

router = APIRouter(prefix="/missions", tags=["missions"])
log = logging.getLogger("missions")

VALID_TRANSITIONS = {
    "pending": {"in_progress", "canceled"},
    "in_progress": {"paused", "completed", "canceled"},
    "paused": {"in_progress", "canceled"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_ACTIVE_MISSIONS: set[int] = set()


async def _begin_exploration(mission_id: int, user_id: int) -> None:
    """Marca la misión en curso y arranca la exploración en el simulador.

    Hay un único dron: al iniciar esta misión, cualquier otra que esté realmente
    EXPLORANDO ('in_progress') queda detenida y se cierra. Las misiones 'paused'
    sobreviven (su estado está persistido y pueden reanudarse o verse en Minecraft),
    así el usuario puede intercalar entre varias minas.
    """
    if mission_id in _ACTIVE_MISSIONS:
        log.info("[misión %s] ya en curso; se ignora la petición duplicada", mission_id)
        return
    _ACTIVE_MISSIONS.add(mission_id)
    try:
        await _run_exploration(mission_id, user_id)
    finally:
        _ACTIVE_MISSIONS.discard(mission_id)


async def _run_exploration(mission_id: int, user_id: int) -> None:
    stop_other_explorations(mission_id)
    reset_controller(mission_id)
    async for db in get_db():
        await db.execute(
            "UPDATE missions SET status = 'completed', end_time = ? "
            "WHERE user_id = ? AND id <> ? AND status = 'in_progress'",
            (_now(), user_id, mission_id),
        )
        await db.commit()

    try:
        ok = await ensure_server_running()
    except Exception as exc:  # noqa: BLE001
        log.error("No se pudo arrancar el servidor para la misión %s: %s", mission_id, exc)
        ok = False
    if not ok:
        # No se toca el estado de la misión: sigue 'paused' (si vino de pausa)
        # o 'pending' (si era nueva), y el usuario puede reintentar.
        return

    async for db in get_db():
        await db.execute(
            "UPDATE missions SET status = 'in_progress', start_time = COALESCE(start_time, ?) "
            "WHERE id = ? AND user_id = ?",
            (_now(), mission_id, user_id),
        )
        await db.commit()

    final_status = "completed"
    try:
        result = await start_exploration(mission_id)
        final_status = result.get("final_status", "completed")
        log.info("Exploración de la misión %s finalizada (spawn=%s, path=%s, final=%s)",
                 mission_id, result.get("spawn"), result.get("path_len"), final_status)
    except ExplorationStopped:
        log.info("Exploración de la misión %s detenida por el usuario", mission_id)
    except Exception as exc:  # noqa: BLE001
        log.error("Exploración de la misión %s interrumpida: %s", mission_id, exc)
        final_status = "completed"

    if final_status == "paused":
        # La misión queda pausada (el endpoint de pausa ya la marcó así) y su
        # checkpoint está persistido: puede reanudarse en cualquier momento,
        # incluso tras reiniciar la máquina. No se toca la BD para no pisar
        # un reanudado inmediato de otra tarea.
        await flush_exploration_state(mission_id)
        return

    # Finalizada (completada o detenida por el usuario): cerrar y limpiar estado.
    await clear_exploration_state(mission_id)
    async for db in get_db():
        await db.execute(
            "UPDATE missions SET status = 'completed', end_time = ? "
            "WHERE id = ? AND user_id = ?",
            (_now(), mission_id, user_id),
        )
        await db.commit()
        await _update_mission_metrics(db, mission_id)


def _fire_exploration(mission_id: int, user_id: int) -> None:
    loop = asyncio.get_running_loop()
    loop.create_task(_begin_exploration(mission_id, user_id))


async def _update_mission_metrics(db, mission_id: int) -> None:
    """Calcula y persiste las métricas de la misión desde los voxeles grabados.

    - risk_detections: vóxeles con riesgo detectado (risk_label > 0).
    - final_entropy: entropía residual media observada (0 = certeza total).
    """
    cur = await db.execute(
        """SELECT
             (SELECT COUNT(*) FROM voxeles WHERE mission_id = ? AND risk_label > 0) AS risk,
             (SELECT AVG(entropy_value) FROM voxeles WHERE mission_id = ?) AS entropy""",
        (mission_id, mission_id),
    )
    row = await cur.fetchone()
    risk = int(row["risk"] or 0)
    entropy = float(row["entropy"]) if row["entropy"] is not None else 0.0
    await db.execute(
        "UPDATE missions SET risk_detections = ?, final_entropy = ? WHERE id = ?",
        (risk, round(entropy, 4), mission_id),
    )
    await db.commit()


async def _get_mission_or_404(db, mission_id: int, user_id: int) -> dict:
    cur = await db.execute(
        """SELECT m.*, mn.name AS mine_name FROM missions m
           LEFT JOIN mines mn ON mn.id = m.mine_id
           WHERE m.id = ? AND m.user_id = ?""",
        (mission_id, user_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Misión no encontrada")
    return dict(row)


async def _get_mine_or_404(db, mine_id: int) -> dict:
    cur = await db.execute("SELECT * FROM mines WHERE id = ?", (mine_id,))
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Mina no encontrada")
    return dict(row)


@router.get("", response_model=list[MissionOut])
async def list_missions(status_filter: str | None = None, user=Depends(get_current_user),
                        db=Depends(get_db)):
    query = """SELECT m.*, mn.name AS mine_name FROM missions m
               LEFT JOIN mines mn ON mn.id = m.mine_id
               WHERE m.user_id = ?"""
    params: list = [user["id"]]
    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    query += " ORDER BY register_date DESC"
    rows = await (await db.execute(query, params)).fetchall()
    return [MissionOut.model_validate(dict(r)) for r in rows]


@router.post("", response_model=MissionOut, status_code=status.HTTP_201_CREATED)
async def create_mission(body: MissionCreate, user=Depends(get_current_user),
                         db=Depends(get_db)):
    await _get_mine_or_404(db, body.mine_id)
    config = body.config_json or {}
    cur = await db.execute(
        "INSERT INTO missions (name, user_id, mine_id, config_json) VALUES (?, ?, ?, ?)",
        (body.name, user["id"], body.mine_id, json.dumps(config)),
    )
    mission_id = cur.lastrowid

    sample_voxels = [
        (mission_id, 0, 0, 0, 1, 0, 0.85, 0.20, 1),
        (mission_id, 1, 0, 0, 1, 0, 0.90, 0.15, 2),
        (mission_id, 0, 1, 0, 2, 1, 0.70, 0.80, 3),
        (mission_id, 0, 0, 1, 1, 0, 0.75, 0.30, 4),
    ]
    await db.executemany(
        """INSERT INTO voxeles (mission_id, coord_x, coord_y, coord_z,
           state, risk_label, confidence, entropy_value, mission_step)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        sample_voxels,
    )
    await db.commit()
    _fire_exploration(mission_id, user["id"])
    return MissionOut.model_validate(await _get_mission_or_404(db, mission_id, user["id"]))


@router.get("/{mission_id}", response_model=MissionOut)
async def get_mission(mission_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    return MissionOut.model_validate(await _get_mission_or_404(db, mission_id, user["id"]))


@router.patch("/{mission_id}", response_model=MissionOut)
async def patch_mission(mission_id: int, body: MissionPatch, user=Depends(get_current_user),
                        db=Depends(get_db)):
    mission = await _get_mission_or_404(db, mission_id, user["id"])
    if body.status and body.status != mission["status"]:
        allowed = VALID_TRANSITIONS.get(mission["status"], set())
        if body.status not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"No se puede pasar de '{mission['status']}' a '{body.status}'",
            )
    fields, values = [], []
    data = body.model_dump(exclude_unset=True)
    if "config_json" in data and data["config_json"] is not None:
        data["config_json"] = json.dumps(data["config_json"])
    for key, value in data.items():
        fields.append(f"{key} = ?")
        values.append(value)
    if fields:
        values.append(mission_id)
        await db.execute(f"UPDATE missions SET {', '.join(fields)} WHERE id = ?", values)
        await db.commit()
    return MissionOut.model_validate(await _get_mission_or_404(db, mission_id, user["id"]))


@router.delete("/{mission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mission(mission_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    await _get_mission_or_404(db, mission_id, user["id"])
    await db.execute("DELETE FROM missions WHERE id = ?", (mission_id,))
    await db.commit()


@router.post("/{mission_id}/start", response_model=MissionOut)
async def start_mission(mission_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    mission = await _get_mission_or_404(db, mission_id, user["id"])
    if mission["status"] == "in_progress":
        return MissionOut.model_validate(mission)
    if mission["status"] == "paused":
        _fire_exploration(mission_id, user["id"])
        return MissionOut.model_validate(mission)
    if mission["status"] not in ("pending",):
        raise HTTPException(status_code=422, detail="La misión no se puede iniciar")

    _fire_exploration(mission_id, user["id"])
    return MissionOut.model_validate(mission)


@router.post("/{mission_id}/pause", response_model=MissionOut)
async def pause_mission(mission_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    mission = await _get_mission_or_404(db, mission_id, user["id"])
    if mission["status"] != "in_progress":
        raise HTTPException(status_code=422, detail="La misión no se puede pausar en este estado")
    if not pause_exploration(mission_id):
        raise HTTPException(status_code=409, detail="No hay una exploración activa en memoria")
    await flush_exploration_state(mission_id)
    await db.execute(
        "UPDATE missions SET status = 'paused' WHERE id = ? AND user_id = ?",
        (mission_id, user["id"]),
    )
    await db.commit()
    await _update_mission_metrics(db, mission_id)
    return MissionOut.model_validate(await _get_mission_or_404(db, mission_id, user["id"]))


@router.post("/{mission_id}/resume", response_model=MissionOut)
async def resume_mission(mission_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Reanuda una misión pausada desde su último punto.

    Siempre relanza una tarea de exploración nueva: regenera el mundo con la seed
    de esa mina (si hace falta), carga el checkpoint persistido y continúa donde
    se quedó. Esto permite reanudar tras reiniciar la máquina y, a la vez,
    intercalar el mapa activo en Minecraft entre varias minas pausadas.
    """
    mission = await _get_mission_or_404(db, mission_id, user["id"])
    if mission["status"] != "paused":
        raise HTTPException(status_code=422, detail="La misión solo se reanuda desde 'paused'")
    _fire_exploration(mission_id, user["id"])
    return MissionOut.model_validate(mission)


@router.post("/{mission_id}/switch", response_model=MissionOut)
async def switch_mission(mission_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    """Carga el mapa de una misión pausada/completada en Minecraft (sin explorar).

    Regenera el mundo con la seed de esa mina y reposiciona el dron en su última
    posición explorada: permite cambiar el mapa que se ve entre minas pausadas.
    """
    mission = await _get_mission_or_404(db, mission_id, user["id"])
    if mission["status"] not in ("paused", "completed"):
        raise HTTPException(
            status_code=422,
            detail="Solo se puede cargar el mapa de una misión pausada o completada",
        )
    other = await db.execute(
        "SELECT id FROM missions WHERE user_id = ? AND id <> ? AND status = 'in_progress'",
        (user["id"], mission_id),
    )
    if await other.fetchone() is not None:
        raise HTTPException(
            status_code=409,
            detail="Pausa primero la exploración activa antes de cambiar de mina",
        )
    ok = await ensure_server_running()
    if not ok:
        raise HTTPException(status_code=503, detail="No se pudo levantar el servidor de Minecraft")
    await switch_world(mission_id)
    return MissionOut.model_validate(mission)


@router.get("/{mission_id}/map")
async def get_mission_map(mission_id: int, band: int | None = None,
                          page: int | None = None, size: int = 50000,
                          user=Depends(get_current_user), db=Depends(get_db)):
    """Devuelve los voxeles de la mision como JSON 3D.

    - band: 0..3 filtra un nivel (banda X del plugin: coord_x/112).
    - page/size: paginacion opcional (size default 50000); sin page devuelve todo.
    """
    mission = await _get_mission_or_404(db, mission_id, user["id"])
    if band is not None and not (0 <= band <= 3):
        raise HTTPException(status_code=422, detail="'band' debe estar entre 0 y 3")

    where = "WHERE mission_id = ?"
    params: list = [mission_id]
    if band is not None:
        where += " AND MIN(3, MAX(0, coord_x / 112)) = ?"
        params.append(band)

    cur = await db.execute(
        f"""SELECT COUNT(*) AS n,
                   MIN(coord_x) AS minx, MAX(coord_x) AS maxx,
                   MIN(coord_y) AS miny, MAX(coord_y) AS maxy,
                   MIN(coord_z) AS minz, MAX(coord_z) AS maxz
            FROM voxeles {where}""",
        params,
    )
    agg = await cur.fetchone()
    total = int(agg["n"] or 0)

    bounds = None
    if total:
        bounds = {
            "min": {"x": agg["minx"], "y": agg["miny"], "z": agg["minz"]},
            "max": {"x": agg["maxx"], "y": agg["maxy"], "z": agg["maxz"]},
        }

    has_more = False
    if page is None:
        cur = await db.execute(
            f"""SELECT coord_x, coord_y, coord_z, state, risk_label,
                       confidence, entropy_value, mission_step
                FROM voxeles {where}
                ORDER BY coord_x, coord_y, coord_z""",
            params,
        )
        rows = await cur.fetchall()
    else:
        if size < 1:
            raise HTTPException(status_code=422, detail="'size' debe ser >= 1")
        offset = page * size
        cur = await db.execute(
            f"""SELECT coord_x, coord_y, coord_z, state, risk_label,
                       confidence, entropy_value, mission_step
                FROM voxeles {where}
                ORDER BY coord_x, coord_y, coord_z LIMIT ? OFFSET ?""",
            params + [size + 1, offset],
        )
        rows = await cur.fetchall()
        has_more = len(rows) > size
        rows = rows[:size]

    return {
        "mission_id": mission_id,
        "total_voxels": total,
        "band": band,
        "page": page,
        "size": size,
        "has_more": has_more,
        "bounds": bounds,
        "voxels": [
            {
                "pos": {"x": r[0], "y": r[1], "z": r[2]},
                "state": r[3],
                "risk": r[4],
                "confidence": r[5],
                "entropy": r[6],
                "step": r[7],
            }
            for r in rows
        ],
    }


@router.post("/{mission_id}/stop", response_model=MissionOut)
async def stop_mission(mission_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    mission = await _get_mission_or_404(db, mission_id, user["id"])
    if mission["status"] not in ("in_progress", "paused"):
        raise HTTPException(status_code=422, detail="La misión no está activa")
    stop_exploration(mission_id)
    await clear_exploration_state(mission_id)
    await db.execute(
        "UPDATE missions SET status = 'completed', end_time = ? WHERE id = ? AND user_id = ?",
        (_now(), mission_id, user["id"]),
    )
    await db.commit()
    await _update_mission_metrics(db, mission_id)
    return MissionOut.model_validate(await _get_mission_or_404(db, mission_id, user["id"]))
