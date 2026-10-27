"""Endpoints de exportación de resultados de una misión.

    GET  /missions/{id}/exports             -> lista los artefactos ya generados
    POST /missions/{id}/export              -> (re)genera todos y devuelve la lista
    GET  /missions/{id}/export/{fmt}        -> genera (si hace falta) y descarga

fmt: csv | ply | obj | mtl | png | pdf
query: metric=entropy|risk|confidence (png), mode=points|mesh (ply), level=0..3,
       refresh=1 (regenerar aunque exista).
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ..config import settings
from ..db import get_db
from ..security import get_current_user
from .missions import _get_mission_or_404

router = APIRouter(prefix="/missions", tags=["exports"])
log = logging.getLogger("exports")

EXPORT_MAX_VOXELS = 500_000
VALID_FMTS = ("csv", "ply", "obj", "mtl", "png", "pdf")
MEDIA_TYPES = {
    "csv": "text/csv",
    "ply": "application/octet-stream",
    "obj": "text/plain",
    "mtl": "text/plain",
    "png": "image/png",
    "pdf": "application/pdf",
}


def _mission_dir(mission_id: int) -> Path:
    return settings.maps_dir / f"mission_{mission_id}"


def _artifact_name(mission_id: int, fmt: str, metric: str) -> str:
    if fmt == "png":
        return f"mission_{mission_id}_heatmap_{metric}.png"
    return f"mission_{mission_id}.{fmt}"


async def _load_mission_data(db, mission_id: int):
    rows = await (await db.execute(
        """SELECT coord_x AS x, coord_y AS y, coord_z AS z, state,
                  risk_label AS risk, confidence, entropy_value AS entropy,
                  mission_step AS step
           FROM voxeles WHERE mission_id = ?
           ORDER BY mission_step, coord_x, coord_y, coord_z""",
        (mission_id,),
    )).fetchall()

    history = await (await db.execute(
        """SELECT mission_step AS step, COUNT(*) AS n, AVG(entropy_value) AS entropy
           FROM voxeles WHERE mission_id = ? AND mission_step IS NOT NULL
           GROUP BY mission_step ORDER BY mission_step""",
        (mission_id,),
    )).fetchall()

    return [dict(r) for r in rows], [dict(h) for h in history]


def _load_cnn_meta() -> dict:
    path = settings.cnn_model_path
    if not path.is_file():
        return {}
    try:
        import torch
        ckpt = torch.load(path, map_location="cpu", weights_only=False)
        return ckpt.get("meta", {}) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo cargar el meta de la CNN (%s): %s", path, exc)
        return {}


def _compute_stats(mission: dict, rows: list[dict]) -> dict:
    from ..exporters import level_band, wall_voxels

    entropies = [r["entropy"] for r in rows if r["entropy"] is not None]
    risk = mission.get("risk_detections")
    if risk is None:
        risk = sum(1 for r in rows if (r["risk"] or 0) > 0)
    entropy = mission.get("final_entropy")
    if entropy is None and entropies:
        entropy = sum(entropies) / len(entropies)
    steps = [r["step"] for r in rows if r["step"] is not None]
    levels = {level_band(r["x"]) for r in rows}
    return {
        "coverage": mission.get("coverage_percent"),
        "final_entropy": round(entropy, 4) if entropy is not None else None,
        "risk_detections": risk,
        "voxels": len(rows),
        "walls": len(wall_voxels(rows)),
        "steps": max(steps) if steps else 0,
        "levels": len(levels),
        "rows": rows,
    }


async def _load_and_guard(db, mission_id: int, user: dict):
    """Carga la misión (con control de propiedad) y sus vóxeles, validando límites."""
    mission = await _get_mission_or_404(db, mission_id, user["id"])
    rows, history = await _load_mission_data(db, mission_id)
    if not rows:
        raise HTTPException(status_code=409, detail="La misión no tiene vóxeles que exportar")
    if len(rows) > EXPORT_MAX_VOXELS:
        raise HTTPException(status_code=413, detail="La misión excede el límite de exportación")
    stats = _compute_stats(mission, rows)
    return mission, rows, history, stats


def _write_artifact(fmt: str, path: Path, mission: dict, stats: dict, history,
                    cnn_meta: dict, metric: str, mode: str, level: int | None) -> None:
    from .. import exporters

    rows = stats["rows"]
    tmp = path.with_name(f"{path.stem}.part{path.suffix}")
    mission_id = mission["id"]
    name = mission.get("name") or f"Misión {mission_id}"

    if fmt == "csv":
        exporters.write_csv(tmp, rows if level is None else exporters.filter_level(rows, level))
    elif fmt == "ply":
        exporters.write_ply(tmp, rows, mission_id, mode=mode, level=level)
    elif fmt == "obj":
        exporters.write_obj(tmp, rows, mission_id, level=level, mtl_name=path.stem)
    elif fmt == "png":
        exporters.render_heatmap_png(tmp, rows, name, metric=metric, level=level)
    elif fmt == "pdf":
        exporters.build_report_pdf(tmp, mission, stats, history, cnn_meta, level=level)
    else:
        raise ValueError(f"Formato no soportado: {fmt}")

    tmp.replace(path)
    # El OBJ escribe un .mtl hermano con el nombre del temporal: renómbralo también.
    part_mtl = tmp.with_suffix(".mtl")
    if part_mtl.is_file():
        part_mtl.replace(path.with_suffix(".mtl"))


async def _ensure_artifact(mission: dict, stats: dict, history, cnn_meta: dict,
                           fmt: str, metric: str, mode: str, level: int | None,
                           refresh: bool) -> Path:
    directory = _mission_dir(mission["id"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _artifact_name(mission["id"], fmt, metric)
    if path.is_file() and not refresh:
        return path
    await asyncio.to_thread(
        _write_artifact, fmt, path, mission, stats, history, cnn_meta, metric, mode, level
    )
    log.info("[misión %s] exportado %s -> %s", mission["id"], fmt, path.name)
    return path


@router.get("/{mission_id}/exports")
async def list_exports(mission_id: int, user=Depends(get_current_user), db=Depends(get_db)):
    mission = await _get_mission_or_404(db, mission_id, user["id"])
    directory = _mission_dir(mission_id)
    files = []
    if directory.is_dir():
        for p in sorted(directory.iterdir()):
            if p.is_file() and ".part." not in p.name:
                st = p.stat()
                files.append({
                    "name": p.name,
                    "format": p.suffix.lstrip("."),
                    "bytes": st.st_size,
                    "modified": int(st.st_mtime),
                })
    return {"mission_id": mission_id, "map_path": mission.get("map_path"), "files": files}


@router.post("/{mission_id}/export")
async def build_all_exports(mission_id: int, user=Depends(get_current_user),
                            db=Depends(get_db)):
    """Genera CSV, PLY, OBJ, PNG (entropía) y PDF y deja la carpeta cacheada."""
    mission = await _get_mission_or_404(db, mission_id, user["id"])
    rows, history = await _load_mission_data(db, mission_id)
    if not rows:
        raise HTTPException(status_code=409, detail="La misión no tiene vóxeles que exportar")
    if len(rows) > EXPORT_MAX_VOXELS:
        raise HTTPException(status_code=413, detail="La misión excede el límite de exportación")
    stats = _compute_stats(mission, rows)
    cnn_meta = await asyncio.to_thread(_load_cnn_meta)

    results = []
    for fmt, metric, mode in (("csv", "entropy", "points"), ("ply", "entropy", "points"),
                              ("obj", "entropy", "points"), ("png", "entropy", "points"),
                              ("pdf", "entropy", "points")):
        path = await _ensure_artifact(mission, stats, history, cnn_meta, fmt, metric, mode,
                                      None, refresh=True)
        results.append({"format": fmt, "name": path.name, "bytes": path.stat().st_size})

    await db.execute("UPDATE missions SET map_path = ? WHERE id = ?",
                     (str(_mission_dir(mission_id)), mission_id))
    await db.commit()
    return {"mission_id": mission_id, "map_path": str(_mission_dir(mission_id)), "files": results}


@router.get("/{mission_id}/export/{fmt}")
async def download_export(mission_id: int, fmt: str,
                          metric: str = Query("entropy"),
                          mode: str = Query("points"),
                          level: int | None = Query(None, ge=0, le=3),
                          refresh: bool = Query(False),
                          user=Depends(get_current_user), db=Depends(get_db)):
    if fmt not in VALID_FMTS:
        raise HTTPException(status_code=422, detail=f"Formato inválido: {fmt}")
    if metric not in ("entropy", "risk", "confidence"):
        raise HTTPException(status_code=422, detail=f"Métrica inválida: {metric}")
    if mode not in ("points", "mesh"):
        raise HTTPException(status_code=422, detail=f"Modo inválido: {mode}")

    mission, rows, history, stats = await _load_and_guard(db, mission_id, user)

    # El .mtl es hermano del .obj: asegura el OBJ (que lo genera) y sírvelo.
    if fmt == "mtl":
        await _ensure_artifact(mission, stats, history, {}, "obj", "entropy", "points",
                               level, refresh)
        mtl = _mission_dir(mission_id) / f"mission_{mission_id}.mtl"
        if not mtl.is_file():
            raise HTTPException(status_code=404, detail="Esta misión no generó .mtl")
        return FileResponse(mtl, media_type=MEDIA_TYPES["mtl"], filename=mtl.name)

    cnn_meta = await asyncio.to_thread(_load_cnn_meta) if fmt == "pdf" else {}
    try:
        path = await _ensure_artifact(mission, stats, history, cnn_meta, fmt, metric, mode,
                                      level, refresh)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if path.stat().st_size == 0:
        raise HTTPException(status_code=500, detail="El archivo exportado quedó vacío")
    return FileResponse(path, media_type=MEDIA_TYPES[fmt], filename=path.name)
