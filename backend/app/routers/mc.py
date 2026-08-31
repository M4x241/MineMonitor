from fastapi import APIRouter, Depends, HTTPException

from ..mc_client import bridge
from ..models import BlocksRequest, GroundTruthRequest, MoveRequest, Pos, RaycastRequest, TeleportRequest
from ..security import get_current_user

router = APIRouter(prefix="/mc", tags=["minecraft-bridge"])


async def _require_connected():
    if not bridge.connected:
        try:
            await bridge.connect()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Servidor de Minecraft no disponible: {exc}",
            ) from exc


@router.get("/status")
async def status(_=Depends(get_current_user)):
    await _require_connected()
    return await bridge.status()


@router.get("/position")
async def position(_=Depends(get_current_user)):
    await _require_connected()
    return await bridge.position()


@router.post("/move")
async def move(body: MoveRequest, _=Depends(get_current_user)):
    await _require_connected()
    return await bridge.move(body.target.model_dump(), body.speed)


@router.post("/teleport")
async def teleport(body: TeleportRequest, _=Depends(get_current_user)):
    await _require_connected()
    return await bridge.teleport(body.position.model_dump())


@router.post("/raycast")
async def raycast(body: RaycastRequest, _=Depends(get_current_user)):
    await _require_connected()
    direction = body.direction.model_dump() if body.direction else None
    return await bridge.raycast(
        body.origin.model_dump(), direction, body.yaw, body.pitch, body.range, body.rays
    )


@router.post("/blocks")
async def blocks(body: BlocksRequest, _=Depends(get_current_user)):
    await _require_connected()
    return await bridge.blocks(body.center.model_dump(), body.radius)


@router.post("/ground_truth")
async def ground_truth(body: GroundTruthRequest, _=Depends(get_current_user)):
    """Verdad de terreno: etiquetas de riesgo por vóxel para entrenar la 3D-CNN."""
    await _require_connected()
    return await bridge.ground_truth(body.center.model_dump(), body.radius)
