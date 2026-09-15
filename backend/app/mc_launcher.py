import asyncio
import json
import logging
import math
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

# Alfabeto de materiales observables en la mina (generador + zonas de riesgo).
# Cada bloque desconocido es una variable aleatoria X_u sobre este alfabeto;
# con prior uniforme, su entropía es H = log2(K) bits.
_MATERIAL_ALPHABET = frozenset({
    "STONE", "COBBLESTONE", "DEEPSLATE",
    "ANDESITE", "DIORITE", "GRANITE", "COBBLED_DEEPSLATE",
    "CRACKED_STONE_BRICKS", "GRAVEL", "SAND", "DIRT", "TUFF",
    "MAGMA_BLOCK", "CAVE_AIR", "AIR", "VOID_AIR", "WATER", "LAVA",
})
_CELL_ENTROPY_BITS = math.log2(len(_MATERIAL_ALPHABET))


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


def _cell_center(cell):
    return {"x": cell[0] + 0.5, "y": cell[1] + 0.5, "z": cell[2] + 0.5}


def _neighbors(cell):
    x, y, z = cell
    return (
        (x + 1, y, z), (x - 1, y, z),
        (x, y + 1, z), (x, y - 1, z),
        (x, y, z + 1), (x, y, z - 1),
    )


async def _scan_memory(center, known: dict, radius: int) -> None:
    """Vuelca los bloques vistos por el WebSocket en el mapa interno known."""
    try:
        resp = await bridge.blocks(center, radius)
    except Exception as exc:  # noqa: BLE001
        log.debug("scan falló en %s: %s", center, exc)
        return
    for blk in resp.get("blocks", []):
        known[(blk["x"], blk["y"], blk["z"])] = blk.get("material", "STONE")


def _known_air(known: dict) -> set:
    return {cell for cell, mat in known.items() if mat in _AIR_MATERIALS}


async def _wait_arrival(timeout: float = 8.0) -> None:
    """Espera a que el dron termine su interpolación (moving == False)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            st = await bridge.status()
            if st and not st.get("moving"):
                return
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.1)


async def _move_and_wait(cell, speed: float = 15.0) -> None:
    await bridge.move(_cell_center(cell), speed=max(1.0, min(speed, 20.0)))
    await _wait_arrival(timeout=8.0)


def _info_gain(cell, known: dict, radius: int = 3, half: int | None = None) -> float:
    """Ganancia de información (bits) de observar 'cell' con el sensor real.

    G(c) = Σ_{u ∈ footprint(c)} H(X_u), H(X_u) = _CELL_ENTROPY_BITS para todo
    bloque u aún desconocido dentro del cubo de radio 'radius' (el mismo sensor
    que usa _scan_memory). Solo cuenta la caja de la mina (x/z ±half, y 10-52)
    para no atraer al dron hacia el exterior. Devuelve 0 si no aporta nada."""
    x, y, z = cell
    bits = 0.0
    for dx in range(-radius, radius + 1):
        ux = x + dx
        if half is not None and abs(ux) > half:
            continue
        for dy in range(-radius, radius + 1):
            uy = y + dy
            if uy < 10 or uy > 52:
                continue
            for dz in range(-radius, radius + 1):
                uz = z + dz
                if half is not None and abs(uz) > half:
                    continue
                u = (ux, uy, uz)
                if u not in known:
                    bits += _CELL_ENTROPY_BITS
    return bits


async def start_exploration(mission_id: int) -> dict:
    """Autómata de exploración por teoría de la información (sin BFS/DFS).

    En cada paso decide moverse a la celda de aire adyacente sin visitar que
    maximiza la ganancia de información ahora mismo: G(c) = Σ H(X_u) sobre el
    footprint del sensor (cubo de radio 3, el mismo que usa _scan_memory),
    evaluado contra el mapa conocido. Sin memoria suficiente (celda sin opciones
    con información pendiente) retrocede rápido por el trail hasta la bifurcación
    más cercana (Trémaux). Cuando el trail se agota sin opciones, la exploración
    terminó sola."""
    status = await bridge.status()
    mine = status.get("mineSpawn", status.get("worldSpawn"))
    half = int(status.get("regionHalf", 48) or 48)
    ctrl = _controller(mission_id)

    start = {"x": mine.get("x", 0.5), "y": mine.get("y", 51.0), "z": mine.get("z", 0.5)}
    await bridge.teleport(start)
    await _record_blocks(mission_id, start, radius=8, step=0)

    known: dict = {}
    await _scan_memory(start, known, radius=3)

    cur = (int(round(start["x"])), int(round(start["y"])), int(round(start["z"])))
    walked = {cur}
    path = [cur]
    forks = set()
    heading = (1, 0, 0)
    max_steps = max(120, half * half)

    def options_at(cell):
        opts = []
        for n in _neighbors(cell):
            if n in walked or n not in known or known[n] not in _AIR_MATERIALS:
                continue
            gain = _info_gain(n, known, half=half)
            if gain > 0:
                opts.append((gain, n))
        return opts

    def pick(opts):
        def key(pair):
            gain, n = pair
            d = (n[0] - cur[0], n[1] - cur[1], n[2] - cur[2])
            align = int(d == heading)
            back = int(d == (-heading[0], -heading[1], -heading[2]))
            return (gain, align, -back)
        return max(opts, key=key)[1]

    step = 0
    complete = False
    while step < max_steps:
        await ctrl.wait_if_paused()
        if ctrl.stopped():
            log.info("[misión %s] detenida por el usuario", mission_id)
            break

        opts = options_at(cur)
        if len(opts) >= 2:
            forks.add(cur)

        if not opts:
            # Callejón sin salida: retrocede rápido por el trail hasta la
            # bifurcación más cercana que aún tenga opciones con información.
            while len(path) > 1:
                await ctrl.wait_if_paused()
                if ctrl.stopped():
                    break
                path.pop()
                prev = path[-1]
                cur = prev
                walked.add(cur)
                await _move_and_wait(prev, speed=20.0)
                if options_at(cur):
                    break
            if len(path) <= 1 and not options_at(cur):
                complete = True
                log.info("[misión %s] exploración completa: sin celdas con información pendiente",
                         mission_id)
                break
            continue

        nxt = pick(opts)
        d = (nxt[0] - cur[0], nxt[1] - cur[1], nxt[2] - cur[2])
        await ctrl.wait_if_paused()
        if ctrl.stopped():
            log.info("[misión %s] detenida por el usuario", mission_id)
            break
        await _move_and_wait(nxt, speed=15.0)
        cur = nxt
        walked.add(cur)
        path.append(cur)
        heading = d
        step += 1

        await _scan_memory(_cell_center(cur), known, radius=3)
        await _record_blocks(mission_id, _cell_center(cur), radius=6, step=step)
        coverage = (len(walked) / max(len(_known_air(known)), 1)) * 100.0
        await _update_progress(mission_id, coverage)
        log.info("[misión %s] paso %d @ %s (mapa %d, forks %d) -> cobertura %.1f%%",
                 mission_id, step, cur, len(known), len(forks), coverage)

    return {"spawn": start, "status": status, "path_len": len(walked), "forks": len(forks),
            "complete": complete}