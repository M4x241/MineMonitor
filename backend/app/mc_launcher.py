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
CHECKPOINT_EVERY = 6  # pasos entre escrituras de checkpoint (batch; flush al pausar/parar)

# Mapas de material -> etiqueta de riesgo (equivalente a MineGenerator.riskLabel)
_RISK_MAP = {
    "CRACKED_STONE_BRICKS": 1,
    "GRAVEL": 2, "SAND": 2, "DIRT": 2,
    "ANDESITE": 3, "DIORITE": 3, "GRANITE": 3, "COBBLED_DEEPSLATE": 3, "TUFF": 3,
    "LAVA": 4, "MAGMA_BLOCK": 4,
}

_AIR_MATERIALS = {"AIR", "CAVE_AIR", "VOID_AIR", "WATER", "LAVA"}

# Margen de vecindad para la 3D-CNN (parche 7x7x7 -> 3 bloques alrededor).
_CNN_PAD = 3

_predictor_instance = None


def _get_risk_predictor():
    """Carga la 3D-CNN entrenada una sola vez (lazy, con fallback a heurística)."""
    global _predictor_instance
    if _predictor_instance is not None or not settings.cnn_enable:
        return _predictor_instance or None
    path = settings.cnn_model_path
    if not path.is_file():
        log.info("Modelo 3D-CNN no encontrado (%s); se usa la heurística de materiales", path)
        _predictor_instance = False
        return None
    try:
        from .cnn_model import RiskPredictor
        _predictor_instance = RiskPredictor(path)
        log.info("3D-CNN cargada (%s)", path)
    except Exception as exc:  # noqa: BLE001
        log.error("No se pudo cargar la 3D-CNN (%s): %s", path, exc)
        _predictor_instance = False
    return _predictor_instance or None

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
    """Control en caliente de una exploración en ejecución (pausa/reanudar/parar).

    'pausa' y 'parar' detienen la tarea en el siguiente chequeo del bucle; la
    diferencia es el estado final ('paused' conserva el checkpoint para reanudar,
    'completed' lo elimina). El estado del autómata se persiste en la BD en cada
    paso, por lo que pausar y apagar la máquina no pierde el progreso: al reanudar
    se lanza una tarea nueva que regenera el mundo de esa mina y continúa.
    """

    def __init__(self, mission_id: int) -> None:
        self.mission_id = mission_id
        self._pause = asyncio.Event()
        self._stop = asyncio.Event()
        self.final_status = "completed"
        self.last_checkpoint: dict | None = None

    def pause(self) -> None:
        self._pause.set()
        self._stop.set()
        self.final_status = "paused"

    def resume(self) -> None:
        self._pause.clear()

    def stop(self) -> None:
        self._stop.set()
        self.final_status = "completed"

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


def reset_controller(mission_id: int) -> ExplorationController:
    """Descarta cualquier controlador viejo (pausado/detenido) y crea uno nuevo."""
    old = _explorations.pop(mission_id, None)
    if old is not None:
        old.stop()
    return _controller(mission_id)


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


# --- Chequeos del estado del autómata (reanudar tras reinicio/apagón) -------

def _cell_key(cell) -> str:
    return f"{cell[0]},{cell[1]},{cell[2]}"


def _cell_from_key(key: str):
    x, y, z = key.split(",")
    return (int(x), int(y), int(z))


async def _save_exploration_state(mission_id: int, state: dict) -> None:
    """Persiste el estado del autómata para poder reanudar donde se quedó."""
    conn = await _db_connect()
    try:
        await conn.execute(
            """INSERT INTO exploration_state (mission_id, state_json, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(mission_id) DO UPDATE SET
                   state_json = excluded.state_json,
                   updated_at = CURRENT_TIMESTAMP""",
            (mission_id, json.dumps(state, separators=(",", ":"))),
        )
        await conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.error("No se pudo guardar el estado de la misión %s: %s", mission_id, exc)
    finally:
        await conn.close()


async def _load_exploration_state(mission_id: int) -> dict | None:
    conn = await _db_connect()
    try:
        cur = await conn.execute(
            "SELECT state_json FROM exploration_state WHERE mission_id = ?", (mission_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return json.loads(row[0])
    finally:
        await conn.close()


async def clear_exploration_state(mission_id: int) -> None:
    conn = await _db_connect()
    try:
        await conn.execute("DELETE FROM exploration_state WHERE mission_id = ?", (mission_id,))
        await conn.commit()
    finally:
        await conn.close()


async def flush_exploration_state(mission_id: int) -> bool:
    """Persiste en la BD el último checkpoint en memoria (para batching).

    Con el guardado por lotes, el autómata solo escribe cada CHECKPOINT_EVERY
    pasos; al pausar/parar esta función vuelca el estado más reciente para no
    perder los pasos intermedios y poder reanudar exactamente donde se quedó.
    """
    ctrl = _explorations.get(mission_id)
    if ctrl is None or not ctrl.last_checkpoint:
        return False
    await _save_exploration_state(mission_id, ctrl.last_checkpoint)
    return True


async def switch_world(mission_id: int) -> dict:
    """Carga la mina de la misión en el mundo de Minecraft sin explorar.

    Regenera el mundo con la seed de la misión (si cambió) y deja el dron en la
    última posición explorada (o en el spawn del nivel 1). Sirve para intercalar
    entre los mapas de misiones pausadas: 'ver en Minecraft'.
    """
    mine_seed = await _mission_mine_seed(mission_id)
    status = await bridge.status()
    if mine_seed and status.get("seed") != mine_seed:
        log.info("[misión %s] switch: regenerando mina con seed=%s (antes: %s)",
                 mission_id, mine_seed, status.get("seed"))
        status = await bridge.regen(mine_seed)

    levels = status.get("mineLevels") or []
    prev = await _load_exploration_state(mission_id)

    pos = None
    if prev and prev.get("cur"):
        c = prev["cur"]
        pos = {"x": c[0] + 0.5, "y": c[1] + 0.5, "z": c[2] + 0.5}
    elif levels:
        spawn = levels[0].get("spawn") or status.get("mineSpawn")
        if spawn:
            pos = {"x": float(spawn.get("x", 0.5)), "y": float(spawn.get("y", 51.0)),
                   "z": float(spawn.get("z", 0.5))}
    elif status.get("mineSpawn"):
        pos = status["mineSpawn"]

    if pos:
        await bridge.teleport(pos)
        log.info("[misión %s] switch: dron reposicionado en %s (seed=%s)",
                 mission_id, pos, status.get("seed"))
    return status


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
    """Consulta los bloques alrededor del dron y los registra como voxeles en la BD.

    Si hay una 3D-CNN cargada, pide un cubo con margen (_CNN_PAD) para tener el
    contexto 7x7x7 de cada bloque sólido y etiqueta riesgo/confianza por
    inferencia (en lote); si no, usa la heurística de materiales.
    """
    predictor = _get_risk_predictor()
    fetch_radius = radius + (_CNN_PAD if predictor else 0)
    try:
        resp = await bridge.blocks(center, fetch_radius)
    except Exception as exc:  # noqa: BLE001
        log.debug("blocks() falló en %s: %s", center, exc)
        return 0

    blocks = resp.get("blocks", [])

    # Inferencia en lote sobre los bloques sólidos (con 7x7x7 de contexto).
    # El trabajo (indice + parches + CNN) es CPU puro: se ejecuta en un hilo
    # aparte para NO bloquear el event-loop de FastAPI (si no, /map y /pause
    # se cuelgan segundos mientras la exploración avanza).
    preds: dict[tuple, tuple] = {}
    if predictor:
        try:
            def _predict_all():
                from .cnn_model import _build_index, build_grid

                index = _build_index(blocks)
                grids, targets = [], []
                for blk in blocks:
                    if blk.get("material") in _AIR_MATERIALS:
                        continue
                    x, y, z = blk["x"], blk["y"], blk["z"]
                    grid = build_grid(index, x, y, z)
                    if (grid == 7).any():
                        continue
                    grids.append(grid)
                    targets.append((x, y, z))
                out = {}
                if grids:
                    labels, confs = predictor.predict_grids(grids)
                    for (x, y, z), lab, conf in zip(targets, labels.tolist(), confs.tolist()):
                        out[(x, y, z)] = (int(lab), float(conf))
                return out

            preds = await asyncio.to_thread(_predict_all)
        except Exception as exc:  # noqa: BLE001
            log.warning("Inferencia 3D-CNN falló (%s); voxeles con heurística", exc)

    rows = []
    for blk in blocks:
        x, y, z = blk["x"], blk["y"], blk["z"]
        material = blk.get("material", "STONE")
        if material in _AIR_MATERIALS:
            state = 1
            risk = 0
            confidence = 0.9
            entropy = 0.15
        else:
            state = 2 if material_name_is_solid(material) else 1
            pred = preds.get((x, y, z))
            if pred is not None:
                risk, confidence = pred
            else:
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


def _info_gain(cell, known: dict, bounds: dict, radius: int = 3) -> float:
    """Ganancia de información (bits) de observar 'cell' con el sensor real.

    G(c) = Σ_{u ∈ footprint(c)} H(X_u), H(X_u) = _CELL_ENTROPY_BITS para todo
    bloque u aún desconocido dentro del cubo de radio 'radius' (el mismo sensor
    que usa _scan_memory). Solo cuenta la caja del nivel activo (bounds) para
    no atraer al dron hacia el exterior ni a otros niveles. Devuelve 0 si no
    aporta nada."""
    x, y, z = cell
    min_x, max_x = bounds["minX"], bounds["maxX"]
    min_y, max_y = bounds["minY"], bounds["maxY"]
    min_z, max_z = bounds["minZ"], bounds["maxZ"]
    bits = 0.0
    for dx in range(-radius, radius + 1):
        ux = x + dx
        if ux < min_x or ux > max_x:
            continue
        for dy in range(-radius, radius + 1):
            uy = y + dy
            if uy < min_y or uy > max_y:
                continue
            for dz in range(-radius, radius + 1):
                uz = z + dz
                if uz < min_z or uz > max_z:
                    continue
                u = (ux, uy, uz)
                if u not in known:
                    bits += _CELL_ENTROPY_BITS
    return bits


async def start_exploration(mission_id: int) -> dict:
    """Autómata de exploración por teoría de la información (sin BFS/DFS).

    La misión puede abarcar varios niveles de mina (4): arranca en el primero y,
    al agotar la información de un nivel, teletransporta el dron al siguiente y
    continúa explorando hasta completar todos. En cada nivel decide moverse a la
    celda de aire adyacente sin visitar que maximiza la ganancia de información
    ahora mismo: G(c) = Σ H(X_u) sobre el footprint del sensor (cubo de radio 3,
    el mismo que usa _scan_memory), evaluado contra el mapa conocido, limitado a
    la caja (bounds) del nivel activo. Sin memoria suficiente (celda sin opciones
    con información pendiente) retrocede rápido por el trail hasta la bifurcación
    más cercana (Trémaux). Cuando el trail se agota sin opciones, el nivel terminó.

    Reanudación: el estado del autómata se guarda como checkpoint en la BD tras
    cada paso. Si ya existe un checkpoint, esta función lo carga y continúa desde
    el mismo nivel/paso (regenerando el mundo con la seed de la mina si hace
    falta), de modo que se puede reanudar tras reiniciar el backend o la máquina.
    """
    # La seed de la mina asociada a la misión define el mundo (regen si cambió).
    mine_seed = await _mission_mine_seed(mission_id)
    ctrl = _controller(mission_id)

    status = await bridge.status()
    if mine_seed is not None and status.get("seed") != mine_seed:
        log.info("[misión %s] regenerando mina con seed=%s (antes: %s)",
                 mission_id, mine_seed, status.get("seed"))
        status = await bridge.regen(mine_seed)

    # Niveles de la mina (cada uno con spawn y caja); fallback a un solo nivel.
    levels = status.get("mineLevels") or []
    if not levels:
        mine = status.get("mineSpawn", status.get("worldSpawn"))
        half = int(status.get("regionHalf", 48) or 48)
        levels = [{
            "name": "mina",
            "spawn": {"x": mine.get("x", 0.5), "y": mine.get("y", 51.0), "z": mine.get("z", 0.5)},
            "bounds": {"minX": -half, "maxX": half, "minY": 10, "maxY": 52, "minZ": -half, "maxZ": half},
        }]

    # Mapa conocido, celdas visitadas y contadores globales a lo largo de niveles.
    known: dict = {}
    walked: set = set()
    forks: set = set()
    step = 0
    complete = True
    level_status: list[dict] = []
    level_start_idx = 0
    resume_cur = None
    resume_path: list = []
    resume_heading = (1, 0, 0)

    # Cargar checkpoint previo (reanudar tras pausa/reinicio/apagón).
    prev = await _load_exploration_state(mission_id)
    if prev:
        try:
            known = {_cell_from_key(k): m for k, m in prev.get("known", {}).items()}
            walked = {_cell_from_key(s) for s in prev.get("walked", [])}
            forks = {_cell_from_key(s) for s in prev.get("forks", [])}
            step = int(prev.get("step", 0))
            complete = prev.get("complete", True)
            level_status = list(prev.get("level_status", []) or [])
            lv = int(prev.get("level_index", 0))
            level_start_idx = lv if lv < len(levels) else len(levels)
            if prev.get("path"):
                resume_cur = tuple(prev["cur"])
                resume_path = [_cell_from_key(s) for s in prev["path"]]
                resume_heading = tuple(prev.get("heading", [1, 0, 0]))
            log.info("[misión %s] checkpoint cargado: nivel=%s/%s pasos=%s mapa=%s visitadas=%s",
                     mission_id, level_start_idx + 1, len(levels), step, len(known), len(walked))
        except Exception as exc:  # noqa: BLE001
            log.warning("[misión %s] checkpoint inválido (%s); se reinicia la exploración", mission_id, exc)
            known, walked, forks, step = {}, set(), set(), 0
            complete = True
            level_status = []
            level_start_idx = 0
            resume_cur = None
            resume_path = []
            resume_heading = (1, 0, 0)

    if level_start_idx >= len(levels):
        log.info("[misión %s] sin niveles restantes que explorar (checkpoint al final)", mission_id)
        await clear_exploration_state(mission_id)
        return {"spawn": status.get("mineSpawn"), "status": status, "path_len": len(walked),
                "forks": len(forks), "complete": True, "levels": level_status,
                "final_status": "completed"}

    for lv_index in range(level_start_idx, len(levels)):
        level = levels[lv_index]
        level_name = level.get("name", "mina")
        spawn = level["spawn"]
        bounds = level["bounds"]
        start = {"x": float(spawn.get("x", 0.5)), "y": float(spawn.get("y", 51.0)),
                 "z": float(spawn.get("z", 0.5))}

        # ¿Continuamos un nivel ya en marcha (checkpoint) o arrancamos uno nuevo?
        continuing = lv_index == level_start_idx and resume_cur is not None and resume_path
        if continuing:
            cur = resume_cur
            path = list(resume_path)
            heading = resume_heading
            log.info("[misión %s] reanudando nivel '%s' desde %s (paso %d, trail %d)",
                     mission_id, level_name, cur, step, len(path))
            await bridge.teleport(_cell_center(cur))
            await _scan_memory(_cell_center(cur), known, radius=3)
        else:
            log.info("[misión %s] nivel '%s': teleport a %s (bounds=%s)",
                     mission_id, level_name, start, bounds)
            await bridge.teleport(start)
            await _record_blocks(mission_id, start, radius=8, step=step)
            await _scan_memory(start, known, radius=3)

            cur = (int(round(start["x"])), int(round(start["y"])), int(round(start["z"])))
            walked.add(cur)
            path = [cur]
            heading = (1, 0, 0)
        level_start_step = step

        span_x = int(bounds["maxX"]) - int(bounds["minX"])
        span_z = int(bounds["maxZ"]) - int(bounds["minZ"])
        max_steps = max(120, span_x * span_z)

        def options_at(cell):
            opts = []
            for n in _neighbors(cell):
                if n in walked or n not in known or known[n] not in _AIR_MATERIALS:
                    continue
                gain = _info_gain(n, known, bounds)
                if gain > 0:
                    opts.append((gain, n))
            return opts

        level_complete = False
        while step < max_steps + level_start_step:
            await ctrl.wait_if_paused()
            if ctrl.stopped():
                log.info("[misión %s] detenida por el usuario en nivel '%s'", mission_id, level_name)
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
                    prev_cell = path[-1]
                    cur = prev_cell
                    walked.add(cur)
                    await _move_and_wait(prev_cell, speed=20.0)
                    if options_at(cur):
                        break
                if len(path) <= 1 and not options_at(cur):
                    level_complete = True
                    log.info("[misión %s] nivel '%s' completo: sin celdas con información pendiente",
                             mission_id, level_name)
                    break
                continue

            def pick(opts):
                def key(pair):
                    gain, n = pair
                    d = (n[0] - cur[0], n[1] - cur[1], n[2] - cur[2])
                    align = int(d == heading)
                    back = int(d == (-heading[0], -heading[1], -heading[2]))
                    return (gain, align, -back)
                return max(opts, key=key)[1]

            nxt = pick(opts)
            d = (nxt[0] - cur[0], nxt[1] - cur[1], nxt[2] - cur[2])
            await ctrl.wait_if_paused()
            if ctrl.stopped():
                log.info("[misión %s] detenida por el usuario en nivel '%s'", mission_id, level_name)
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

            # Checkpoint en memoria; se persiste por lotes (y flush al pausar/parar).
            checkpoint = {
                "seed": mine_seed,
                "level_index": lv_index,
                "level_status": level_status,
                "step": step,
                "known": {_cell_key(c): m for c, m in known.items()},
                "walked": [_cell_key(c) for c in walked],
                "forks": [_cell_key(c) for c in forks],
                "path": [_cell_key(c) for c in path],
                "heading": list(heading),
                "cur": list(cur),
                "complete": complete,
            }
            ctrl.last_checkpoint = checkpoint
            if step - level_start_step == 0 or step % CHECKPOINT_EVERY == 0:
                await _save_exploration_state(mission_id, checkpoint)

        level_status.append({"name": level_name, "steps": step - level_start_step,
                             "complete": level_complete})
        if ctrl.stopped():
            complete = False
            break

        # Nivel terminado: avanzar el checkpoint al siguiente nivel (arranque limpio).
        next_idx = lv_index + 1
        next_checkpoint = {
            "seed": mine_seed,
            "level_index": next_idx,
            "level_status": level_status,
            "step": step,
            "known": {_cell_key(c): m for c, m in known.items()},
            "walked": [_cell_key(c) for c in walked],
            "forks": [_cell_key(c) for c in forks],
            "path": [],
            "heading": [1, 0, 0],
            "cur": [0, 0, 0],
            "complete": True,
        }
        ctrl.last_checkpoint = next_checkpoint
        await _save_exploration_state(mission_id, next_checkpoint)

    return {"spawn": status.get("mineSpawn"), "status": status, "path_len": len(walked),
            "forks": len(forks), "complete": complete, "levels": level_status,
            "final_status": ctrl.final_status}


async def _mission_mine_seed(mission_id: int) -> str | None:
    """Seed de la mina a la que pertenece la misión (para regenerar el mundo)."""
    conn = await _db_connect()
    try:
        cur = await conn.execute(
            """SELECT mn.seed FROM missions m
               JOIN mines mn ON mn.id = m.mine_id
               WHERE m.id = ?""",
            (mission_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else None
    finally:
        await conn.close()