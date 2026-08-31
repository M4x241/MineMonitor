import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from ..db import get_db
from ..mc_client import bridge
from ..models import MissionCreate, MissionOut, MissionPatch
from ..security import get_current_user

router = APIRouter(prefix="/missions", tags=["missions"])

VALID_TRANSITIONS = {
    "pending": {"in_progress", "canceled"},
    "in_progress": {"paused", "completed", "canceled"},
    "paused": {"in_progress", "canceled"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_mission_or_404(db, mission_id: int, user_id: int) -> dict:
    cur = await db.execute(
        "SELECT * FROM missions WHERE id = ? AND user_id = ?", (mission_id, user_id)
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Misión no encontrada")
    return dict(row)


async def _get_mission_or_404_public(db, mission_id: int) -> dict:
    cur = await db.execute("SELECT * FROM missions WHERE id = ?", (mission_id,))
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
async def list_missions(status_filter: str | None = None, db=Depends(get_db)):
    query = "SELECT * FROM missions"
    params: list = []
    if status_filter:
        query += " WHERE status = ?"
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
    await db.commit()
    return MissionOut.model_validate(await _get_mission_or_404(db, cur.lastrowid, user["id"]))


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
    if mission["status"] not in ("pending", "paused"):
        raise HTTPException(status_code=422, detail="La misión no se puede iniciar")
    mine = await _get_mine_or_404(db, mission["mine_id"])

    await bridge.connect()
    await bridge.teleport({
        "x": mine["spawn_x"] + 0.5,
        "y": mine["spawn_y"] + 1.0,
        "z": mine["spawn_z"] + 0.5,
    })

    await db.execute(
        "UPDATE missions SET status = 'in_progress', start_time = COALESCE(start_time, ?) WHERE id = ?",
        (_now(), mission_id),
    )
    await db.commit()
    return MissionOut.model_validate(await _get_mission_or_404(db, mission_id, user["id"]))


@router.get("/{mission_id}/map")
async def get_mission_map(mission_id: int, db=Depends(get_db)):
    """Devuelve todos los voxeles de la mision como JSON 3D para visualizacion."""
    mission = await _get_mission_or_404_public(db, mission_id)
    cur = await db.execute(
        """SELECT coord_x, coord_y, coord_z, state, risk_label,
                  confidence, entropy_value, mission_step
           FROM voxeles WHERE mission_id = ?""",
        (mission_id,),
    )
    rows = await cur.fetchall()

    # Envolvertura minima del mapa
    if not rows:
        return {"mission_id": mission_id, "voxels": [], "bounds": None}

    xs = [r[0] for r in rows]
    ys = [r[1] for r in rows]
    zs = [r[2] for r in rows]

    return {
        "mission_id": mission_id,
        "total_voxels": len(rows),
        "bounds": {
            "min": {"x": min(xs), "y": min(ys), "z": min(zs)},
            "max": {"x": max(xs), "y": max(ys), "z": max(zs)},
        },
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
    await db.execute(
        "UPDATE missions SET status = 'completed', end_time = ? WHERE id = ?",
        (_now(), mission_id),
    )
    await db.commit()
    return MissionOut.model_validate(await _get_mission_or_404(db, mission_id, user["id"]))
