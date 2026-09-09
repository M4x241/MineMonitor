import asyncio
import json
import logging
from pathlib import Path

import aiosqlite

from .config import settings
from .mc_client import bridge

log = logging.getLogger("mc_launcher")

MC_START_TIMEOUT = 120.0
WS_POLL_INTERVAL = 2.0

# Mapas de material -> etiqueta de riesgo (equivalente a MineGenerator.riskLabel)
_RISK_MAP = {
    "CRACKED_STONE_BRICKS": 1,
    "GRAVEL": 2, "SAND": 2, "DIRT": 2,
    "ANDESITE": 3, "DIORITE": 3, "GRANITE": 3, "COBBLED_DEEPSLATE": 3, "TUFF": 3,
    "LAVA": 4, "MAGMA_BLOCK": 4,
}

_AIR_MATERIALS = {"AIR", "CAVE_AIR", "VOID_AIR", "WATER", "LAVA"}

_NEIGHBOR_OFFSETS = [
    (1, 0, 0), (-1, 0, 0),
    (0, 0, 1), (0, 0, -1),
    (0, 1, 0), (0, -1, 0),
]


class ExplorationStopped(Exception):
    """Se lanza cuando el usuario detiene/finaliza una exploración en marcha."""


class ExplorationController:
    """Control en caliente de una exploración en ejecución (pausa/reanudar/parar)."""

    def __init__(self, mission_id: int) -> None:
        self.mission_id = mission_id
        self._pause = asyncio.Event()
        self._stop = asyncio.Event()

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def stop(self) -> None:
        self._stop.set()

    def stopped(self) -> bool:
        return self._stop.is_set()

    async def wait_if_paused(self) -> None:
        while self._pause.is_set() and not self._stop.is_set():
            await asyncio.sleep(0.2)


_explorations: dict[int, ExplorationController] = {}


def _controller(mission_id: int) -> ExplorationController:
    if mission_id not in _explorations:
        _explorations[mission_id] = ExplorationController(mission_id)
    return _explorations[mission_id]


def pause_exploration(mission_id: int) -> bool:
    ctrl = _explorations.get(mission_id)
    if ctrl is None or ctrl.stopped():
        return False
    ctrl.pause()
    return True


def resume_exploration(mission_id: int) -> bool:
    ctrl = _explorations.get(mission_id)
    if ctrl is None or ctrl.stopped():
        return False
    ctrl.resume()
    return True


def stop_exploration(mission_id: int) -> bool:
    ctrl = _explorations.get(mission_id)
    if ctrl is None:
        return False
    ctrl.stop()
    return True


def stop_other_explorations(keep_mission_id: int) -> None:
    """Frena en caliente cualquier exploración activa distinta de la misión dada.

    Hay un único dron en el servidor de Minecraft, así que solo puede haber una
    exploración en marcha a la vez: al iniciar una misión nueva, las demás quedan
    detenidas para que 'esa misma' sea la que realmente explore.
    """
    for mid, ctrl in list(_explorations.items()):
        if mid != keep_mission_id and not ctrl.stopped():
            log.info("[misión %s] detenida porque se inició la misión %s", mid, keep_mission_id)
            ctrl.stop()


def server_jar() -> str:
    return str(settings.server_dir / "paper.jar")


async def server_ready_now() -> bool:
    """True si el WebSocket del plugin ya responde (servidor arriba y cargado)."""
    return await bridge.is_ready()


async def _wait_ws_ready(timeout: float = MC_START_TIMEOUT) -> None:
    """Espera (con reintentos) a que el WebSocket del plugin acepte conexión."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            await bridge.connect()
            if bridge.connected:
                return
        except Exception as exc:  # noqa: BLE001
            log.debug("WS aún no disponible: %s", exc)
        await asyncio.sleep(WS_POLL_INTERVAL)
    raise TimeoutError("El servidor de Minecraft no abrió su WebSocket a tiempo")


async def _launch_server_process() -> asyncio.subprocess.Process:
    log_path = settings.server_dir / "mc_launcher.log"
    logf = open(log_path, "w", encoding="utf-8")
    proc = await asyncio.create_subprocess_exec(
        "java",
        "-Xms1G",
        "-Xmx3G",
        "-jar",
        "paper.jar",
        "nogui",
        cwd=str(settings.server_dir),
        stdout=logf,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    log.info("Servidor Paper lanzado (pid=%s, jar=%s)", proc.pid, server_jar())
    return proc


async def ensure_server_running(timeout: float = MC_START_TIMEOUT) -> bool:
    """Garantiza que el servidor MC esté arriba y su WebSocket listo."""
    if not settings.server_dir.joinpath("paper.jar").is_file():
        log.error("paper.jar no encontrado en %s; no se puede arrancar el servidor",
                  settings.server_dir)
        return False

    if await server_ready_now():
        log.info("Servidor de Minecraft ya estaba disponible")
        return True

    await _launch_server_process()
    await _wait_ws_ready(timeout=timeout)
    log.info("Servidor de Minecraft listo")
    return True


async def _db_connect() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(settings.db_path)
    await conn.execute("PRAGMA journal_mode = WAL")
    return conn


async def _record_blocks(mission_id: int, center: dict, radius: int, step: int) -> int:
    """Consulta los bloques alrededor del dron y los registra como voxeles en la BD."""
    try:
        resp = await bridge.blocks(center, radius)
    except Exception as exc:  # noqa: BLE001
        log.debug("blocks() falló en %s: %s", center, exc)
        return 0

    rows = []
    for blk in resp.get("blocks", []):
        x, y, z = blk["x"], blk["y"], blk["z"]
        material = blk.get("material", "STONE")
        if material in _AIR_MATERIALS:
            state = 1
            risk = 0
            confidence = 0.9
            entropy = 0.15
        else:
            state = 2 if material_name_is_solid(material) else 1
            risk = _RISK_MAP.get(material, 0)
            confidence = 0.75 if risk == 0 else 0.6
            entropy = 0.9 if risk else 0.3
        rows.append((mission_id, x, y, z, state, risk, confidence, entropy, step))

    conn = await _db_connect()
    try:
        await conn.executemany(
            """INSERT INTO voxeles
               (mission_id, coord_x, coord_y, coord_z, state, risk_label,
                confidence, entropy_value, mission_step)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(mission_id, coord_x, coord_y, coord_z)
               DO UPDATE SET state=excluded.state,
                             risk_label=excluded.risk_label,
                             confidence=excluded.confidence,
                             entropy_value=excluded.entropy_value,
                             mission_step=excluded.mission_step,
                             last_updated=CURRENT_TIMESTAMP""",
            rows,
        )
        await conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.error("No se pudieron registrar voxeles: %s", exc)
    finally:
        await conn.close()
    return len(rows)


def material_name_is_solid(material: str) -> bool:
    """Heurística simple: los materiales no aire/solido tratan como bloques duros."""
    return material not in _AIR_MATERIALS and not material.endswith("_LEAVES")


async def _update_progress(mission_id: int, coverage: float) -> None:
    conn = await _db_connect()
    try:
        await conn.execute(
            "UPDATE missions SET coverage_percent = ? WHERE id = ?", (round(coverage, 2), mission_id)
        )
        await conn.commit()
    finally:
        await conn.close()


async def start_exploration(mission_id: int) -> dict:
    """Despliega/posiciona el dron al inicio de la cueva y recorre la red de
    túneles de la mina (solo por celdas de aire) recogiendo voxeles en la BD.
    Es un autómata celda a celda: consulta los bloques locales, elige el
    siguiente paso entre las celdas libres con mayor información (ramas sin
    explorar, preferencia a seguir recto) y retrocede en callejones sin salida."""
    status = await bridge.status()
    mine = status.get("mineSpawn", status.get("worldSpawn"))
    half = int(status.get("regionHalf", 48) or 48)

    # Arrancar SIEMPRE en el interior de la mina (mineSpawn, nivel de la galería:
    # startY-3). worldSpawn es la superficie del mundo; el mob puede estar perdido
    # en un terreno abierto o dentro de un bloque por misiones previas.
    start = {"x": mine.get("x", 0.5), "y": mine.get("y", 51.0), "z": mine.get("z", 0.5)}
    await bridge.teleport(start)
    await _record_blocks(mission_id, start, radius=8, step=0)

    max_steps = max(40, half * half // 3)
    visited = set()
    stack = []
    heading = (1, 0, 0)
    cur = (int(round(start["x"])), int(round(start["y"])), int(round(start["z"])))
    visited.add(cur)
    stack.append(cur)

    ctrl = _controller(mission_id)
    for step in range(1, max_steps + 1):
        await ctrl.wait_if_paused()
        if ctrl.stopped():
            log.info("[misión %s] detenida por el usuario", mission_id)
            break
        ahead = (cur[0] + heading[0], cur[1] + heading[1], cur[2] + heading[2])
        next_cell = await _choose_next_cell(cur, visited, heading)
        if next_cell is None:
            if len(stack) <= 1:
                log.info("[misión %s] sin celdas libres nuevas; terminando", mission_id)
                break
            stack.pop()
            back = stack[-1]
            await bridge.move({"x": back[0] + 0.5, "y": back[1] + 0.5, "z": back[2] + 0.5},
                              speed=6.0)
            await asyncio.sleep(2.0)
            continue

        await bridge.move({"x": next_cell[0] + 0.5, "y": next_cell[1] + 0.5,
                           "z": next_cell[2] + 0.5}, speed=6.0)
        await asyncio.sleep(2.0)
        cur = next_cell
        visited.add(cur)
        stack.append(cur)
        if ahead == next_cell:
            heading = tuple(heading)

        center = {"x": cur[0] + 0.5, "y": cur[1] + 0.5, "z": cur[2] + 0.5}
        recorded = await _record_blocks(mission_id, center, radius=8, step=step)
        coverage = (len(visited) / max_steps) * 100.0
        await _update_progress(mission_id, coverage)
        log.info("[misión %s] paso %d/%d @ %s (%d bloques) -> cobertura %.1f%%",
                 mission_id, step, max_steps, (cur[0], cur[1], cur[2]), recorded, coverage)

    return {"spawn": start, "status": status, "path_len": len(visited)}


async def _choose_next_cell(cell, visited, heading):
    """Consulta los bloques alrededor y elige la siguiente celda libre con más
    información: prioriza ramas no visitadas y seguir recto, evita girar y
    volver sobre sí mismo. Devuelve (x,y,z) o None si no hay paso libre."""
    center = {"x": cell[0] + 0.5, "y": cell[1] + 0.5, "z": cell[2] + 0.5}
    try:
        resp = await bridge.blocks(center, radius=1)
    except Exception as exc:  # noqa: BLE001
        log.debug("blocks() falló en %s: %s", center, exc)
        return None

    free = set()
    for blk in resp.get("blocks", []):
        k = (blk["x"], blk["y"], blk["z"])
        if k == cell or blk.get("material", "STONE") in _AIR_MATERIALS:
            free.add(k)

    candidates = []
    for ox, oy, oz in _NEIGHBOR_OFFSETS:
        n = (cell[0] + ox, cell[1] + oy, cell[2] + oz)
        if n in free:
            candidates.append(n)

    # Ordena: ramas nunca visitadas primero; dentro de un grupo, dar preferencia
    # a seguir en la misma dirección del heading (menos giro).
    def score(n):
        dx, dy, dz = n[0] - cell[0], n[1] - cell[1], n[2] - cell[2]
        straight = (dx, dy, dz) == tuple(heading)
        unvisited = n not in visited
        back = (dx, dy, dz) == (-heading[0], -heading[1], -heading[2])
        return (int(unvisited), int(straight), int(not back))

    best = max(candidates, key=score, default=None)
    return best