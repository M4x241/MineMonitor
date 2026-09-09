import asyncio
import json
import logging
import uuid

import websockets
from websockets.protocol import State

from .config import settings

log = logging.getLogger("mc_bridge")


class MCBridgeClient:
    """Cliente WebSocket que habla con el plugin de Paper dentro de Minecraft."""

    def __init__(self, url: str):
        self.url = url
        self.ws: websockets.WebSocketClientProtocol | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._listener_task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return bool(self.ws is not None and self.ws.state is State.OPEN)

    async def connect(self) -> None:
        if self.connected:
            return
        self.ws = await websockets.connect(self.url, open_timeout=5)
        self._listener_task = asyncio.create_task(self._listener())
        log.info("Conectado al plugin de Minecraft en %s", self.url)

    async def _listener(self) -> None:
        while self.ws is not None and self.ws.state is State.OPEN:
            try:
                raw = await self.ws.recv()
            except websockets.ConnectionClosed:
                log.warning("Conexión con el plugin cerrada")
                self.ws = None
                break
            except Exception:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            fut = self._pending.pop(msg.get("id"), None)
            if fut is not None and not fut.done():
                fut.set_result(msg)

    async def request(self, payload: dict, timeout: float = 10.0) -> dict:
        msg_id = uuid.uuid4().hex
        payload["id"] = msg_id
        async with self._lock:
            if not self.connected:
                await self.connect()
            fut = asyncio.get_running_loop().create_future()
            self._pending[msg_id] = fut
            await self.ws.send(json.dumps(payload))
        try:
            resp = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise TimeoutError(f"El plugin no respondió a '{payload.get('type')}'")
        if not resp.get("ok", False):
            raise RuntimeError(resp.get("error", "error desconocido del plugin"))
        return resp.get("result", {})

    async def is_ready(self) -> bool:
        if self.connected:
            return True
        try:
            await self.connect()
            return self.connected
        except Exception:  # noqa: BLE001
            return False

    async def status(self) -> dict:
        return await self.request({"type": "status"})

    async def position(self) -> dict:
        return await self.request({"type": "position"})

    async def move(self, target: dict, speed: float = 0.5) -> dict:
        return await self.request({"type": "move", "target": target, "speed": speed})

    async def teleport(self, position: dict) -> dict:
        return await self.request({"type": "teleport", "position": position})

    async def raycast(self, origin: dict, direction: dict | None = None,
                      yaw: float | None = None, pitch: float | None = None,
                      range: float = 16, rays: int = 0) -> dict:
        payload = {"type": "raycast", "origin": origin, "range": range, "rays": rays}
        if direction is not None:
            payload["direction"] = direction
        if yaw is not None:
            payload["yaw"] = yaw
        if pitch is not None:
            payload["pitch"] = pitch
        return await self.request(payload)

    async def blocks(self, center: dict, radius: int = 8) -> dict:
        return await self.request({"type": "blocks", "center": center, "radius": radius})

    async def ground_truth(self, center: dict, radius: int = 8) -> dict:
        return await self.request({"type": "ground_truth", "center": center, "radius": radius})

    async def close(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
        if self.ws is not None:
            await self.ws.close()


bridge = MCBridgeClient(settings.mc_ws_url)
