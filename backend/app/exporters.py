"""Exportación de resultados de una misión (mapa + métricas).

Formatos soportados:
    - CSV : volcado tabular de todos los vóxeles (x,y,z,state,risk,...).
    - PLY : nube de puntos (con color por riesgo) o malla de cubos de las paredes.
    - OBJ : malla de las caras expuestas de las paredes (+ .mtl con un color por
            nivel de riesgo).
    - PNG : heatmap cenital (XZ) de entropía / riesgo / confianza.
    - PDF : reporte de la misión (portada + métricas, heatmap, evolución por paso
            y la evaluación de la 3D-CNN con su matriz de confusión).

El trabajo es CPU puro y se invoca desde el router con `asyncio.to_thread`, por lo
que aquí no hay nada de asincronía ni dependencias de FastAPI.
"""
from __future__ import annotations

import csv
import threading
from collections import defaultdict
from pathlib import Path

# matplotlib solo se importa al usar el módulo (lo hace el router de forma perezosa),
# para no penalizar el arranque de FastAPI. Backend sin display.
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

# Paleta por etiqueta de riesgo (misma que el visor 3D).
RISK_COLORS = {
    0: (0x8E, 0x8E, 0x96),  # piedra
    1: (0x9C, 0x4F, 0x3D),  # grieta
    2: (0xA0, 0x8A, 0x5E),  # grava / tierra
    3: (0x7A, 0x7A, 0x94),  # escombros
    4: (0xFF, 0x6A, 0x3D),  # lava / magma
}
RISK_NAMES = ["seguro", "grieta", "escombro", "derrubio", "lava/magma"]

# pyplot no es thread-safe: serialize PNG/PDF entre peticiones concurrentes.
_MPL_LOCK = threading.Lock()

# Direcciones y caras de un cubo unitario (para OBJ/PLY en malla).
_NEIGHBORS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
# Para cada dirección, los 4 offsets (0/1) de las esquinas de esa cara.
_CUBE_CORNERS = (
    (0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1),  # -Y
    (0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0),  # +Y
    (0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0),  # -X
    (1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1),  # +X
    (0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0),  # -Z
    (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),  # +Z
)
# Orden de las caras anterior: -Y, +Y, -X, +X, -Z, +Z.
_FACE_DIRS = ((0, -1, 0), (0, 1, 0), (-1, 0, 0), (1, 0, 0), (0, 0, -1), (0, 0, 1))


# --------------------------------------------------------------------- helpers

def level_band(x: int) -> int:
    """Banda/nivel de la mina a partir de coord_x (coincide con el plugin: x/112)."""
    b = x // 112
    return 0 if b < 0 else 3 if b > 3 else b


def filter_level(rows, level: int | None):
    if level is None:
        return rows
    return [r for r in rows if level_band(r["x"]) == level]


def _solid_and_air(rows):
    solid = {(r["x"], r["y"], r["z"]) for r in rows if r["state"] == 2}
    air = {(r["x"], r["y"], r["z"]) for r in rows if r["state"] == 1}
    return solid, air


def wall_voxels(rows):
    """Vóxeles sólidos con al menos un vecino de aire (las 'paredes' del visor)."""
    solid, air = _solid_and_air(rows)
    walls = []
    for r in rows:
        if r["state"] != 2:
            continue
        p = (r["x"], r["y"], r["z"])
        if any((p[0] + d[0], p[1] + d[1], p[2] + d[2]) in air for d in _NEIGHBORS):
            walls.append(r)
    return walls or [r for r in rows if r["state"] == 2]


def _risk_color(risk: int):
    return RISK_COLORS.get(int(risk or 0), RISK_COLORS[0])


# ------------------------------------------------------------------------- CSV

def write_csv(path: Path, rows) -> int:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["x", "y", "z", "state", "risk", "confidence", "entropy", "step"])
        for r in rows:
            w.writerow([
                r["x"], r["y"], r["z"], r["state"], r["risk"],
                "" if r["confidence"] is None else r["confidence"],
                "" if r["entropy"] is None else r["entropy"],
                "" if r["step"] is None else r["step"],
            ])
    return len(rows)


# -------------------------------------------------------------------- PLY

def write_ply(path: Path, rows, mission_id: int, mode: str = "points", level: int | None = None) -> int:
    """PLY ASCII. mode='points' -> nube coloreada; mode='mesh' -> cubos de pared."""
    rows = filter_level(rows, level)
    if mode == "mesh":
        return _write_ply_mesh(path, wall_voxels(rows), mission_id)
    return _write_ply_points(path, rows, mission_id)


def _write_ply_points(path: Path, rows, mission_id: int) -> int:
    with open(path, "w", encoding="ascii") as fh:
        fh.write("ply\nformat ascii 1.0\n")
        fh.write(f"comment Mine Explorer - mission {mission_id} - point cloud\n")
        fh.write(f"element vertex {len(rows)}\n")
        fh.write("property float x\nproperty float y\nproperty float z\n")
        fh.write("property uchar red\nproperty uchar green\nproperty uchar blue\n")
        fh.write("end_header\n")
        for r in rows:
            cr, cg, cb = _risk_color(r["risk"])
            fh.write(f"{r['x']} {r['y']} {r['z']} {cr} {cg} {cb}\n")
    return len(rows)


def _write_ply_mesh(path: Path, rows, mission_id: int) -> int:
    with open(path, "w", encoding="ascii") as fh:
        n = len(rows)
        fh.write("ply\nformat ascii 1.0\n")
        fh.write(f"comment Mine Explorer - mission {mission_id} - voxel mesh\n")
        fh.write(f"element vertex {n * 8}\n")
        fh.write("property float x\nproperty float y\nproperty float z\n")
        fh.write("property uchar red\nproperty uchar green\nproperty uchar blue\n")
        fh.write(f"element face {n * 6}\n")
        fh.write("property list uchar int vertex_indices\n")
        fh.write("end_header\n")
        for r in rows:
            x, y, z = r["x"], r["y"], r["z"]
            cr, cg, cb = _risk_color(r["risk"])
            for ox, oy, oz in ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1),
                               (0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)):
                fh.write(f"{x + ox} {y + oy} {z + oz} {cr} {cg} {cb}\n")
        for i in range(n):
            base = i * 8
            for f in range(6):
                a = base + f * 4
                fh.write(f"4 {a} {a + 1} {a + 2} {a + 3}\n")
    return len(rows)


# -------------------------------------------------------------------- OBJ

def write_obj(path: Path, rows, mission_id: int, level: int | None = None,
              mtl_name: str | None = None) -> int:
    """Malla OBJ con solo las caras expuestas (culling de caras internas) + .mtl."""
    rows = filter_level(rows, level)
    solid = {(r["x"], r["y"], r["z"]): r for r in rows if r["state"] == 2}
    verts: dict[tuple, int] = {}
    lines: list[str] = []

    def vidx(p):
        i = verts.get(p)
        if i is None:
            i = len(verts) + 1
            verts[p] = i
            lines.append(p)
        return i

    faces_by_risk: dict[int, list[tuple[int, int, int, int]]] = defaultdict(list)
    for (x, y, z), r in solid.items():
        risk = int(r["risk"] or 0)
        for face, d in enumerate(_FACE_DIRS):
            if (x + d[0], y + d[1], z + d[2]) in solid:
                continue  # cara interna
            idx = []
            for k in range(4):
                ox, oy, oz = _CUBE_CORNERS[face * 4 + k]
                idx.append(vidx((x + ox, y + oy, z + oz)))
            faces_by_risk[risk].append((idx[0], idx[1], idx[2], idx[3]))

    with open(path, "w", encoding="ascii") as fh:
        fh.write(f"# Mine Explorer - mission {mission_id} - voxel walls\n")
        fh.write(f"mtllib {mtl_name or path.stem}.mtl\n")
        for p in lines:
            fh.write(f"v {p[0]} {p[1]} {p[2]}\n")
        fh.write(f"o mission_{mission_id}\n")
        for risk in sorted(faces_by_risk):
            fh.write(f"usemtl risk_{risk}\n")
            for a, b, c, d in faces_by_risk[risk]:
                fh.write(f"f {a} {b} {c} {d}\n")

    _write_mtl(path.with_suffix(".mtl"))
    return len(solid)


def _write_mtl(path: Path) -> None:
    with open(path, "w", encoding="ascii") as fh:
        fh.write("# Mine Explorer - materiales por nivel de riesgo\n")
        for risk, (r, g, b) in RISK_COLORS.items():
            fh.write(f"\nnewmtl risk_{risk}\n")
            fh.write("Ka 0 0 0\nKd %.3f %.3f %.3f\nKs 0 0 0\n" % (r / 255, g / 255, b / 255))


# -------------------------------------------------------------------- PNG / heatmap

_METRICS = {
    "entropy": ("inferno", "Entropía de Shannon residual", "entropy"),
    "risk": ("turbo", "Riesgo (0=seguro .. 4=lava)", "risk"),
    "confidence": ("viridis", "Confianza del modelo", "confidence"),
}


def _heatmap_grid(rows):
    xs = [r["x"] for r in rows]
    zs = [r["z"] for r in rows]
    minx, maxx = min(xs), max(xs)
    minz, maxz = min(zs), max(zs)
    w, h = maxx - minx + 1, maxz - minz + 1
    return minx, minz, w, h


def _accumulate(rows, key, w, h, minx, minz):
    total = np.zeros((h, w), dtype=np.float64)
    count = np.zeros((h, w), dtype=np.float64)
    for r in rows:
        v = r[key]
        if v is None:
            continue
        iz, ix = r["z"] - minz, r["x"] - minx
        total[iz, ix] += float(v)
        count[iz, ix] += 1.0
    mean = np.divide(total, count, out=np.zeros_like(total), where=count > 0)
    return mean, count


def _draw_heatmap(ax, rows, metric: str):
    cmap, label, key = _METRICS[metric]
    minx, minz, w, h = _heatmap_grid(rows)
    grid, count = _accumulate(rows, key, w, h, minx, minz)
    masked = np.ma.masked_where(count == 0, grid)
    im = ax.imshow(masked, origin="lower", cmap=cmap, aspect="equal",
                   extent=(minx - 0.5, minx + w - 0.5, minz - 0.5, minz + h - 0.5))
    ax.set_xlabel("X")
    ax.set_ylabel("Z")
    ax.set_title(f"{label} (media por columna XZ)")
    return im


def render_heatmap_png(path: Path, rows, mission_name: str, metric: str = "entropy",
                       level: int | None = None) -> int:
    rows = filter_level(rows, level)
    if not rows:
        raise ValueError("La misión no tiene vóxeles para dibujar el heatmap")
    metric = metric if metric in _METRICS else "entropy"
    with _MPL_LOCK:
        fig, ax = plt.subplots(figsize=(9, 8))
        im = _draw_heatmap(ax, rows, metric)
        fig.colorbar(im, ax=ax, shrink=0.8, label=_METRICS[metric][1])
        title = mission_name + (f" - nivel {level + 1}" if level is not None else "")
        fig.suptitle(f"{title}  ({len(rows)} vóxeles)", fontsize=12)
        fig.tight_layout()
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
    return len(rows)


# -------------------------------------------------------------------- PDF

def _fmt(v, suffix=""):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}{suffix}"
    return f"{v}{suffix}"


def build_report_pdf(path: Path, mission: dict, stats: dict, history, cnn_meta: dict,
                     level: int | None = None) -> None:
    with _MPL_LOCK, PdfPages(path) as pdf:
        _pdf_cover(pdf, mission, stats)
        if stats.get("voxels"):
            _pdf_heatmap(pdf, mission, stats, level)
            _pdf_history(pdf, mission, history)
        _pdf_cnn(pdf, cnn_meta)


def _pdf_cover(pdf, mission, stats):
    fig = plt.figure(figsize=(8.27, 11.69))  # A4
    fig.suptitle("Mine Explorer - Reporte de misión", fontsize=18, y=0.965)
    fig.text(0.5, 0.915, mission.get("name") or f"Misión {mission.get('id')}",
             ha="center", fontsize=14)

    info = [
        ["Misión", str(mission.get("id"))],
        ["Mina", str(mission.get("mine_name") or mission.get("mine_id"))],
        ["Usuario", str(mission.get("user_id"))],
        ["Estado", str(mission.get("status"))],
        ["Registrada", str(mission.get("register_date") or "—")],
        ["Inicio", str(mission.get("start_time") or "—")],
        ["Fin", str(mission.get("end_time") or "—")],
    ]
    metrics = [
        ["Cobertura (%)", _fmt(stats.get("coverage"))],
        ["Entropía residual media", _fmt(stats.get("final_entropy"))],
        ["Detecciones de riesgo", _fmt(stats.get("risk_detections"))],
        ["Vóxeles totales", _fmt(stats.get("voxels"))],
        ["Vóxeles de pared", _fmt(stats.get("walls"))],
        ["Pasos de exploración", _fmt(stats.get("steps"))],
        ["Niveles de mina", _fmt(stats.get("levels"))],
    ]

    ax1 = fig.add_axes([0.08, 0.60, 0.84, 0.24]); ax1.axis("off")
    t1 = ax1.table(cellText=info, colWidths=[0.35, 0.65], loc="center", cellLoc="left")
    t1.auto_set_font_size(False); t1.set_fontsize(10); t1.scale(1, 1.6)
    ax1.set_title("Datos de la misión", fontsize=12, pad=12)

    ax2 = fig.add_axes([0.08, 0.30, 0.84, 0.26]); ax2.axis("off")
    t2 = ax2.table(cellText=metrics, colWidths=[0.55, 0.45], loc="center", cellLoc="left")
    t2.auto_set_font_size(False); t2.set_fontsize(10); t2.scale(1, 1.6)
    ax2.set_title("Métricas de exploración", fontsize=12, pad=12)

    fig.text(0.5, 0.07,
             "Generado automáticamente por Mine Explorer  |  "
             "Exploración autónoma con teoría de la información y 3D-CNN",
             ha="center", fontsize=8, color="#555555")
    pdf.savefig(fig)
    plt.close(fig)


def _pdf_heatmap(pdf, mission, stats, level):
    rows = stats["rows"]
    rows = filter_level(rows, level)
    if not rows:
        return
    fig, ax = plt.subplots(figsize=(8.27, 8))
    im = _draw_heatmap(ax, rows, "entropy")
    fig.colorbar(im, ax=ax, shrink=0.8, label=_METRICS["entropy"][1])
    fig.suptitle("Mapa de entropía residual (vista cenital XZ)", fontsize=13)
    fig.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def _pdf_history(pdf, mission, history):
    if not history:
        return
    steps = [h["step"] for h in history]
    new_vox = [h["n"] for h in history]
    ent = [h["entropy"] for h in history]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8.27, 8), sharex=True)
    ax1.bar(steps, new_vox, color="#3a6ea5", width=0.9)
    ax1.set_ylabel("Vóxeles nuevos / paso")
    ax1.set_title("Evolución de la exploración por paso", fontsize=13)

    ax2.plot(steps, ent, color="#c0392b", linewidth=1.2)
    ax2.set_ylabel("Entropía media")
    ax2.set_xlabel("Paso de misión")
    ax2.grid(True, alpha=0.3)
    fig.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def _pdf_cnn(pdf, cnn_meta: dict):
    if not cnn_meta:
        return
    confusion = np.array(cnn_meta.get("confusion") or [])
    names = cnn_meta.get("class_names") or RISK_NAMES

    fig = plt.figure(figsize=(8.27, 11.69))
    fig.suptitle("Evaluación de la 3D-CNN de segmentación de riesgo", fontsize=15, y=0.96)

    fig.text(0.5, 0.915,
             f"val accuracy = {cnn_meta.get('val_acc')}  |  parches = {cnn_meta.get('n')}  |  "
             f"patch = {cnn_meta.get('patch')}³  |  clases = {cnn_meta.get('classes')}",
             ha="center", fontsize=10)

    if confusion.size:
        n = confusion.shape[0]
        axc = fig.add_axes([0.15, 0.45, 0.7, 0.4])
        im = axc.imshow(confusion, cmap="Blues")
        axc.set_xticks(range(n), names, rotation=30, ha="right")
        axc.set_yticks(range(n), names)
        axc.set_xlabel("Predicho")
        axc.set_ylabel("Real")
        axc.set_title("Matriz de confusión (validación)")
        for i in range(n):
            for j in range(n):
                axc.text(j, i, int(confusion[i, j]), ha="center", va="center",
                         color="white" if confusion[i, j] > confusion.max() / 2 else "black",
                         fontsize=9)
        fig.colorbar(im, ax=axc, shrink=0.8)

        rows = []
        for c in range(n):
            tp = confusion[c, c]
            real = confusion[c, :].sum()
            pred = confusion[:, c].sum()
            prec = tp / pred if pred else 0.0
            rec = tp / real if real else 0.0
            f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
            rows.append([names[c], int(real), f"{prec:.3f}", f"{rec:.3f}", f"{f1:.3f}"])
        ax2 = fig.add_axes([0.15, 0.10, 0.7, 0.25]); ax2.axis("off")
        t = ax2.table(cellText=rows,
                      colLabels=["Clase", "Reales", "Precisión", "Recall", "F1"],
                      loc="center", cellLoc="center")
        t.auto_set_font_size(False); t.set_fontsize(9); t.scale(1, 1.6)
        ax2.set_title("Precisión / recall por clase", fontsize=12, pad=10)

    fig.text(0.5, 0.045,
             "Entrenado con seeds: " + ", ".join(str(s) for s in (cnn_meta.get("trained_on") or [])),
             ha="center", fontsize=7, color="#555555")
    pdf.savefig(fig)
    plt.close(fig)
