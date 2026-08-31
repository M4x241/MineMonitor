#!/usr/bin/env python3
"""
Generador de cuevas fake 3D para la base de datos de Mine Explorer.

Tipos de cueva:
  cave   - Cellular automata con camara (default, mision 3)
  maze   - Laberinto con ramificaciones y callejones sin salida (mision 1)
  vdrop  - Dos entradas que convergen en V con caida vertical (mision 2)

Uso:
    python gen_fake_cave.py --mission-id 1 --type maze --seed 10
    python gen_fake_cave.py --mission-id 2 --type vdrop --seed 20
    python gen_fake_cave.py --mission-id 3 --type cave --seed 42
"""

import argparse
import random
import sqlite3
import sys
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "mine_explorer.db"


# ── Helpers ──────────────────────────────────────────────────────────

def _empty_grid(w, h, d, val=1):
    return [[[val] * d for _ in range(h)] for _ in range(w)]


def _in_bounds(x, y, z, w, h, d):
    return 0 <= x < w and 0 <= y < h and 0 <= z < d


def _carve_sphere(grid, cx, cy, cz, r, w, h, d):
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            for dz in range(-r, r + 1):
                if dx*dx + dy*dy + dz*dz <= r * r:
                    nx, ny, nz = cx + dx, cy + dy, cz + dz
                    if _in_bounds(nx, ny, nz, w, h, d):
                        grid[nx][ny][nz] = 0


def _carve_box(grid, x1, y1, z1, x2, y2, z2, w, h, d):
    for x in range(max(0, x1), min(w, x2 + 1)):
        for y in range(max(0, y1), min(h, y2 + 1)):
            for z in range(max(0, z1), min(d, z2 + 1)):
                grid[x][y][z] = 0


def _count_solid_neighbors(grid, x, y, z, w, h, d, radius=2):
    count = 0
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                nx, ny, nz = x + dx, y + dy, z + dz
                if _in_bounds(nx, ny, nz, w, h, d):
                    count += grid[nx][ny][nz]
    return count


# ── Tipo: CAVE (cellular automata) ──────────────────────────────────

def gen_cave(w, h, d, seed):
    grid = _empty_grid(w, h, d, 0)
    if seed is not None:
        random.seed(seed)
    for x in range(w):
        for y in range(h):
            for z in range(d):
                if random.random() < 0.45:
                    grid[x][y][z] = 1
    for _ in range(5):
        ng = _empty_grid(w, h, d, 0)
        for x in range(w):
            for y in range(h):
                for z in range(d):
                    n = 0
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            for dz in (-1, 0, 1):
                                if dx == 0 and dy == 0 and dz == 0:
                                    continue
                                nx, ny, nz = x+dx, y+dy, z+dz
                                if _in_bounds(nx, ny, nz, w, h, d):
                                    n += grid[nx][ny][nz]
                                else:
                                    n += 1
                    ng[x][y][z] = 1 if n >= 5 else 0
        grid = ng
    # Túnel principal + cámaras
    cx, cy, cz = 0, h // 2, d // 2
    while cx < w:
        _carve_sphere(grid, cx, cy, cz, 2, w, h, d)
        cx += 1
        if random.random() < 0.6:
            cy += random.choice([-1, 0, 0, 1])
            cy = max(2, min(h - 3, cy))
        if random.random() < 0.4:
            cz += random.choice([-1, 0, 0, 1])
            cz = max(2, min(d - 3, cz))
    for _ in range(4):
        _carve_sphere(grid,
                      random.randint(w//4, 3*w//4),
                      random.randint(h//4, 3*h//4),
                      random.randint(d//4, 3*d//4),
                      random.randint(3, 5), w, h, d)
    return grid


# ── Tipo: MAZE (laberinto ramificado) ───────────────────────────────

def gen_maze(w, h, d, seed):
    """Laberinto con túneles que se ramifican, callejones sin salida,
    intersecciones en T y pasillos que se cruzan."""
    if seed is not None:
        random.seed(seed)

    grid = _empty_grid(w, h, d, 1)  # todo sólido

    # Túnel principal: de x=0 a x=w-1 serpenteando
    cx, cy, cz = 0, h // 2, d // 2
    r = 2  # radio túnel
    main_path = []
    while cx < w:
        _carve_sphere(grid, cx, cy, cz, r, w, h, d)
        main_path.append((cx, cy, cz))
        cx += 1
        if random.random() < 0.5:
            cy += random.choice([-1, 0, 0, 1])
            cy = max(r + 1, min(h - r - 2, cy))
        if random.random() < 0.4:
            cz += random.choice([-1, 0, 0, 1])
            cz = max(r + 1, min(d - r - 2, cz))

    # Ramificaciones desde el camino principal
    branches = []
    for i in range(0, len(main_path), random.randint(4, 8)):
        bx, by, bz = main_path[i]
        direction = random.choice(['+z', '-z', '+y', '-y'])
        length = random.randint(8, 20)
        branches.append(_carve_branch(grid, bx, by, bz, direction, length, r, w, h, d))

    # Sub-ramificaciones desde las ramas
    for branch_start, branch_dir, branch_len in branches:
        for j in range(0, branch_len, random.randint(5, 10)):
            # Punto a lo largo de la rama
            if branch_dir == '+z':
                sx, sy, sz = branch_start[0], branch_start[1], branch_start[2] + j
            elif branch_dir == '-z':
                sx, sy, sz = branch_start[0], branch_start[1], branch_start[2] - j
            elif branch_dir == '+y':
                sx, sy, sz = branch_start[0], branch_start[1] + j, branch_start[2]
            else:
                sx, sy, sz = branch_start[0], branch_start[1] - j, branch_start[2]
            if not _in_bounds(sx, sy, sz, w, h, d):
                continue
            # Sub-rama perpendicular
            sub_dir = random.choice(['+x', '-x'])
            _carve_branch(grid, sx, sy, sz, sub_dir, random.randint(4, 12), r - 1, w, h, d)

    # Callejones sin salida (dead ends) más amplios
    for _ in range(random.randint(3, 6)):
        dx = random.randint(2, w - 3)
        dy = random.randint(r + 1, h - r - 2)
        dz = random.randint(r + 1, d - r - 2)
        if grid[dx][dy][dz] == 0:
            _carve_sphere(grid, dx, dy, dz, random.randint(2, 3), w, h, d)

    # Cámaras de intersección (donde se cruzan túneles)
    for _ in range(5):
        _carve_sphere(grid,
                      random.randint(5, w - 6),
                      random.randint(r + 2, h - r - 3),
                      random.randint(r + 2, d - r - 3),
                      random.randint(2, 4), w, h, d)

    return grid


def _carve_branch(grid, sx, sy, sz, direction, length, r, w, h, d):
    """Cava una rama en la dirección dada. Retorna (start, direction, length)."""
    cx, cy, cz = sx, sy, sz
    for step in range(length):
        _carve_sphere(grid, cx, cy, cz, r, w, h, d)
        if direction == '+z':
            cz += 1
        elif direction == '-z':
            cz -= 1
        elif direction == '+y':
            cy += 1
        elif direction == '-y':
            cy -= 1
        elif direction == '+x':
            cx += 1
        elif direction == '-x':
            cx -= 1
        if not _in_bounds(cx, cy, cz, w, h, d):
            break
    return ((sx, sy, sz), direction, length)


# ── Tipo: VDROP (caída vertical con dos entradas en V) ──────────────

def gen_vdrop(w, h, d, seed):
    """Dos entradas en la parte superior que convergen en forma de V
    hacia un punto central con caída vertical."""
    if seed is not None:
        random.seed(seed)

    grid = _empty_grid(w, h, d, 1)

    # Entrada 1: desde (5, h-2, 5) bajando en diagonal hacia el centro
    # Entrada 2: desde (5, h-2, d-6) bajando en diagonal hacia el centro
    # Punto de convergencia: (w//2, h//3, d//2)

    cx_converge = w // 2
    cy_converge = h // 3
    cz_converge = d // 2

    r = 2

    # Entrada izquierda (z bajo)
    _carve_path_vdrop(grid, 5, h - 3, 5,
                      cx_converge, cy_converge, cz_converge,
                      r, w, h, d, side=0)

    # Entrada derecha (z alto)
    _carve_path_vdrop(grid, 5, h - 3, d - 6,
                      cx_converge, cy_converge, cz_converge,
                      r, w, h, d, side=1)

    # Cámara de convergencia (punto donde se juntan las dos entradas)
    _carve_sphere(grid, cx_converge, cy_converge, cz_converge, 4, w, h, d)

    # Caida vertical desde la convergencia hacia abajo
    for y in range(cy_converge, 2, -1):
        _carve_sphere(grid, cx_converge, y, cz_converge, r, w, h, d)

    # Camara inferior al fondo de la caida
    _carve_sphere(grid, cx_converge, 2, cz_converge, 4, w, h, d)

    # Túnel de salida desde el fondo hacia +x
    for x in range(cx_converge, min(w, cx_converge + 20)):
        _carve_sphere(grid, x, 2, cz_converge, r - 1, w, h, d)
        if random.random() < 0.3:
            cz_converge += random.choice([-1, 0, 1])
            cz_converge = max(r + 1, min(d - r - 2, cz_converge))

    # Ramificaciones laterales desde cada entrada
    _carve_side_branches(grid, 5, h - 3, 5, cx_converge, cy_converge, cz_converge,
                         r, w, h, d, seed_z_offset=0)
    _carve_side_branches(grid, 5, h - 3, d - 6, cx_converge, cy_converge, cz_converge,
                         r, w, h, d, seed_z_offset=1)

    # Cámaras extra en las paredes
    for _ in range(4):
        _carve_sphere(grid,
                      random.randint(3, w - 4),
                      random.randint(3, h - 4),
                      random.randint(r + 2, d - r - 3),
                      random.randint(2, 3), w, h, d)

    return grid


def _carve_path_vdrop(grid, x1, y1, z1, x2, y2, z2, r, w, h, d, side=0):
    """Cava un camino desde (x1,y1,z1) hasta (x2,y2,z2) con curvas."""
    steps = max(abs(x2 - x1), abs(y2 - y1), abs(z2 - z1)) * 2
    for i in range(steps + 1):
        t = i / max(steps, 1)
        cx = int(x1 + (x2 - x1) * t)
        cy = int(y1 + (y2 - y1) * t)
        cz = int(z1 + (z2 - z1) * t)
        # Añadir oscilación lateral según el side
        if side == 0:
            cz += int(2 * (0.5 - abs(t - 0.5)) * 3)
        else:
            cz -= int(2 * (0.5 - abs(t - 0.5)) * 3)
        _carve_sphere(grid, cx, cy, cz, r, w, h, d)


def _carve_side_branches(grid, x1, y1, z1, x2, y2, z2, r, w, h, d, seed_z_offset):
    """Ramas laterales desde una de las entradas hacia las paredes."""
    for _ in range(random.randint(2, 4)):
        # Punto medio de la entrada
        t = random.uniform(0.2, 0.7)
        bx = int(x1 + (x2 - x1) * t)
        by = int(y1 + (y2 - y1) * t)
        bz = int(z1 + (z2 - z1) * t)
        # Ramificar hacia los lados
        direction = random.choice(['+z', '-z'])
        for step in range(random.randint(5, 15)):
            _carve_sphere(grid, bx, by, bz, r - 1, w, h, d)
            if direction == '+z':
                bz += 1
            else:
                bz -= 1
            if not _in_bounds(bx, by, bz, w, h, d):
                break


# ── Risk + Voxel builders (compartidos) ─────────────────────────────

def assign_risk(grid, w, h, d):
    risk = [[[0] * d for _ in range(h)] for _ in range(w)]
    for x in range(w):
        for y in range(h):
            for z in range(d):
                if grid[x][y][z] == 1:
                    risk[x][y][z] = -1
                    continue
                n = _count_solid_neighbors(grid, x, y, z, w, h, d, 2)
                if n >= 80:
                    risk[x][y][z] = 2
                elif n >= 50:
                    risk[x][y][z] = 1
                else:
                    risk[x][y][z] = 0
    for _ in range(int(w * 0.08)):
        rx, ry, rz = random.randint(1, w-2), random.randint(1, h-2), random.randint(1, d-2)
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                for dz in range(-1, 2):
                    nx, ny, nz = rx+dx, ry+dy, rz+dz
                    if _in_bounds(nx, ny, nz, w, h, d):
                        if grid[nx][ny][nz] == 0 and risk[nx][ny][nz] != -1:
                            risk[nx][ny][nz] = 3
    return risk


def build_voxels(grid, risk, w, h, d, mission_id):
    voxels = []
    for x in range(w):
        for y in range(h):
            for z in range(d):
                if grid[x][y][z] == 1:
                    continue
                r = risk[x][y][z]
                if r == -1:
                    continue
                state = 2 if r == 3 else 0
                entropy = 1.0 if state == 0 else round(random.uniform(0.1, 0.4), 4)
                confidence = 0.0 if state == 0 else round(random.uniform(0.6, 0.95), 4)
                voxels.append((mission_id, x, y, z, state, max(0, r), confidence, entropy, None))
    return voxels


def mark_explored(grid, risk, w, h, d, mission_id, n_steps=20):
    explored = []
    for x in range(min(n_steps, w)):
        for y in range(h):
            for z in range(d):
                if grid[x][y][z] == 0 and risk[x][y][z] not in (-1, 3):
                    explored.append((mission_id, x, y, z))
    return explored


# ── Main ─────────────────────────────────────────────────────────────

GENMAP = {
    'cave': gen_cave,
    'maze': gen_maze,
    'vdrop': gen_vdrop,
}


def main():
    parser = argparse.ArgumentParser(description="Genera cuevas fake 3D en la DB")
    parser.add_argument("--mission-id", type=int, required=True)
    parser.add_argument("--type", choices=GENMAP.keys(), default='cave')
    parser.add_argument("--width", type=int, default=64)
    parser.add_argument("--height", type=int, default=32)
    parser.add_argument("--depth", type=int, default=64)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--db", type=str, default=str(DB_PATH))
    args = parser.parse_args()

    w, h, d = args.width, args.height, args.depth
    print(f"[{args.type}] Generando cueva {w}x{h}x{d} para mision {args.mission_id}...")

    t0 = time.time()
    grid = GENMAP[args.type](w, h, d, args.seed)
    risk = assign_risk(grid, w, h, d)
    voxels = build_voxels(grid, risk, w, h, d, args.mission_id)
    explored = mark_explored(grid, risk, w, h, d, args.mission_id)
    t1 = time.time()

    print(f"  Generada en {t1-t0:.2f}s")
    print(f"  Voxel totales: {len(voxels)} (explorados: {len(explored)})")
    for label, name in [(0, "seguro"), (1, "medio"), (2, "alto"), (3, "escombros")]:
        print(f"  Riesgo {label} ({name}): {sum(1 for v in voxels if v[5] == label)}")

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    m = cur.execute("SELECT id, status FROM missions WHERE id = ?", (args.mission_id,)).fetchone()
    if m is None:
        print(f"ERROR: Mision {args.mission_id} no existe")
        sys.exit(1)

    old = cur.execute("SELECT COUNT(*) FROM voxeles WHERE mission_id = ?",
                      (args.mission_id,)).fetchone()[0]
    if old > 0:
        cur.execute("DELETE FROM voxeles WHERE mission_id = ?", (args.mission_id,))
        print(f"  Limpiados {old} voxeles anteriores")

    cur.executemany(
        """INSERT INTO voxeles
           (mission_id, coord_x, coord_y, coord_z, state, risk_label,
            confidence, entropy_value, mission_step)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        voxels
    )

    if explored:
        cur.executemany(
            """UPDATE voxeles SET state = 1, confidence = 0.8,
               entropy_value = 0.2, mission_step = 1
               WHERE mission_id = ? AND coord_x = ? AND coord_y = ? AND coord_z = ?""",
            explored
        )

    total_air = len(voxels)
    total_explored = len(explored)
    coverage = round(100.0 * total_explored / total_air, 2) if total_air > 0 else 0
    cur.execute(
        """UPDATE missions SET
           coverage_percent = ?,
           risk_detections = (SELECT COUNT(*) FROM voxeles
                              WHERE mission_id = ? AND risk_label IN (1,2,3)),
           status = 'in_progress',
           start_time = COALESCE(start_time, CURRENT_TIMESTAMP)
           WHERE id = ?""",
        (coverage, args.mission_id, args.mission_id)
    )

    conn.commit()
    final = cur.execute("SELECT COUNT(*) FROM voxeles WHERE mission_id = ?",
                        (args.mission_id,)).fetchone()[0]
    conn.close()
    print(f"  Listo: {final} voxeles | Cobertura: {coverage}%")


if __name__ == "__main__":
    main()
