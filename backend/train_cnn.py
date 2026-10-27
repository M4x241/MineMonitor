"""Genera el dataset volumetrico desde el simulador y entrena la 3D-CNN.

Uso (desde backend/):
    ./.venv/bin/python train_cnn.py

Recorre varias seeds (regen determinista), hace barridos (lattice) de ground_truth
por nivel juntando parches 7x7x7 etiquetados por la verdad de terreno del plugin,
balancea por clase, entrena y guarda el checkpoint en data/models/cnn_mina.pt.
Al terminar restaura el mundo a la mina del usuario.
"""
import asyncio
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.cnn_model import RiskCnn3D, build_grid, encode_patch
from app.config import settings
from app.mc_client import bridge

PATCH = 7
USER_MINED_SEED = "1419792764"
# Variedad de semillas para cubrir también escombros (clase 3) y lava (clase 4).
TRAIN_SEEDS = [
    "1419792764", "2024073101", "998877665", "3141592653",
    "584301002", "7070707", "123489012", "900912345",
]
RISK_CAP = 350   # max parches de riesgo por mina
SAFE_CAP = 400   # max parches seguros por mina
LATTICE_STEP = 8
MODEL_DIR = settings.cnn_model_path.parent
CLASS_NAMES = ["seguro", "grieta", "escombro", "derrubio", "lava/magma"]


def _augment_orientations(grid: np.ndarray) -> list:
    """Variantes D4 del cubo (rotaciones/reflejos horizontales) para enriquecer
    clases raras sin llamadas extra al simulador."""
    out = {grid.tobytes(): grid}
    base = grid
    for k in range(1, 4):
        base = np.rot90(base, k=1, axes=(0, 2))
        out[base.tobytes()] = base
    out[grid[:, :, ::-1].tobytes()] = grid[:, :, ::-1]
    out[grid[:, ::-1, :].tobytes()] = grid[:, ::-1, :]
    out[grid[:, ::-1, ::-1].tobytes()] = grid[:, ::-1, ::-1]
    return list(out.values())


async def _centered_blocks(x, y, z):
    resp = await bridge.ground_truth({"x": x, "y": y, "z": z}, 3)
    center = next((b for b in resp.get("blocks", []) if (b["x"], b["y"], b["z"]) == (x, y, z)), None)
    return resp.get("blocks", []), center


async def build_mine_dataset(seed: str) -> tuple[list, list]:
    await bridge.regen(seed)
    st = await bridge.status()
    levels = st.get("mineLevels") or []

    risk_coords: dict[tuple, int] = {}
    safe_grids: list = []

    # Barrido lattice: cubos centrados aportan parches seguros y candidatos de
    # riesgo (vecinos etiquetados dentro del 7x7x7 consultado).
    def lattice_heights(b):
        span = b["maxY"] - b["minY"]
        ys = list(range(b["minY"] + 2, b["maxY"] - 2, 4))
        if span >= 10:
            mid = (b["minY"] + b["maxY"]) // 2
            if mid not in ys:
                ys.append(mid)
        return ys or [(b["minY"] + b["maxY"]) // 2]

    lattice = []
    for lv in levels:
        b = lv["bounds"]
        xs = range(b["minX"] + 3, b["maxX"] - 2, LATTICE_STEP)
        zs = range(b["minZ"] + 3, b["maxZ"] - 2, LATTICE_STEP)
        for y in lattice_heights(b):
            for x in xs:
                for z in zs:
                    lattice.append((x, y, z))
    random.shuffle(lattice)

    for cx, cy, cz in lattice:
        blocks, center = await _centered_blocks(cx, cy, cz)
        if center is None or len(blocks) < PATCH ** 3:
            continue
        grid = build_grid(blocks, cx, cy, cz)
        if center.get("risk", 0) == 0:
            if len(safe_grids) < SAFE_CAP * 6:
                safe_grids.append(grid)
        else:
            for blk in blocks:
                if blk["risk"] > 0:
                    key = (blk["x"], blk["y"], blk["z"])
                    risk_coords.setdefault(key, blk["risk"])

    # Parches centrados en los bloques de riesgo detectados.
    risk_pts = list(risk_coords.items())
    if len(risk_pts) > RISK_CAP:
        risk_pts = random.sample(risk_pts, RISK_CAP)

    risk_samples: list[tuple[np.ndarray, int]] = []
    for (cx, cy, cz), _lab in risk_pts:
        blocks, center = await _centered_blocks(cx, cy, cz)
        if center is None:
            continue
        grid = build_grid(blocks, cx, cy, cz)
        if (grid == 7).any():
            continue
        risk_samples.append((grid, center["risk"]))

    # Aumentación de las clases raras mediante orientaciones del cubo.
    aug_grids: list = []
    aug_labels: list = []
    for grid, lab in risk_samples:
        for variant in _augment_orientations(grid):
            aug_grids.append(variant)
            aug_labels.append(lab)
    risk_samples = list(zip(aug_grids, aug_labels)) or risk_samples

    return safe_grids, risk_samples


def balance_dataset(safe_grids, risk_samples, seed=42) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    Xs, ys = [], []
    for grid in safe_grids:
        Xs.append(grid)
        ys.append(0)
    for grid, lab in risk_samples:
        Xs.append(grid)
        ys.append(lab)
    X = np.stack(Xs)
    y = np.array(ys, dtype=np.int64)

    counts = np.bincount(y, minlength=5)
    # Clase 0 (seguro) capeada; clases raras se conservan todas (ya aumentadas).
    per_class = {0: 1200, 1: 600, 2: 600, 3: 600, 4: 600}
    keep = []
    for cls in range(5):
        idx = np.where(y == cls)[0]
        if len(idx) == 0:
            continue
        take = min(len(idx), per_class.get(cls, 400))
        keep.append(rng.choice(idx, size=take, replace=False))
    keep = np.concatenate(keep) if keep else np.array([], dtype=np.int64)
    rng.shuffle(keep)
    return X[keep], y[keep]


@torch.inference_mode()
def evaluate(model, X, y, device):
    model.eval()
    Xt = torch.from_numpy(np.stack([encode_patch(g) for g in X])).to(device)
    yt = torch.from_numpy(y).to(device)
    pred = model(Xt).argmax(dim=1)
    return (pred == yt).sum().item() / len(y)


def train(X, y, epochs: int = 26, batch: int = 128):
    device = torch.device("cpu")
    n = len(y)
    split = int(n * 0.85)
    idx = np.random.default_rng(7).permutation(n)
    xtr, ytr = X[idx[:split]], y[idx[:split]]
    xva, yva = X[idx[split:]], y[idx[split:]]

    counts = np.bincount(ytr, minlength=5)
    weights = 1.0 / (counts.astype(np.float32) + 1.0)
    weights = weights / weights.sum() * len(np.unique(ytr))
    crit = nn.CrossEntropyLoss(weight=torch.from_numpy(weights))

    model = RiskCnn3D().to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.StepLR(opt, step_size=8, gamma=0.5)

    Xtr = torch.from_numpy(np.stack([encode_patch(g) for g in xtr])).to(device)
    ytr = torch.from_numpy(ytr).to(device)
    best_acc, best_state = 0.0, None

    for epoch in range(1, epochs + 1):
        model.train()
        perm = torch.randperm(len(Xtr))
        total, hits, run_loss = 0, 0, 0.0
        for s in range(0, len(Xtr), batch):
            b = perm[s:s + batch]
            logits = model(Xtr[b])
            loss = crit(logits, ytr[b])
            opt.zero_grad()
            loss.backward()
            opt.step()
            preds = logits.argmax(dim=1)
            total += len(b)
            hits += (preds == ytr[b]).sum().item()
            run_loss += loss.item() * len(b)
        sched.step()
        acc = evaluate(model, xva, yva, device)
        if acc > best_acc:
            best_acc = acc
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        print(f"epoch {epoch:02d} | loss {run_loss / total:.4f} | train acc "
              f"{hits / total:.3f} | val acc {acc:.3f}", flush=True)

    # Métricas finales sobre validación con el mejor checkpoint.
    final = RiskCnn3D().to(device)
    final.load_state_dict(best_state or model.state_dict())
    final.eval()
    with torch.inference_mode():
        Xva = torch.from_numpy(np.stack([encode_patch(g) for g in xva])).to(device)
        predv = final(Xva).argmax(dim=1).cpu().numpy()

    cm = np.zeros((5, 5), dtype=np.int64)
    for t, p in zip(yva, predv):
        cm[t, p] += 1

    report = [f"CNN 3D - reporte de entrenamiento ({time.strftime('%Y-%m-%d %H:%M:%S')})",
              f"parches balanceados: {len(y)} | train: {len(ytr)} | val: {len(yva)}",
              f"val accuracy (mejor epoch): {best_acc:.4f}",
              f"distribución total: {np.bincount(y, minlength=5).tolist()}",
              ""]
    report.append("matriz de confusión (filas=real, cols=pred):")
    header = "        " + "".join(f"{CLASS_NAMES[c][:9]:>9}" for c in range(5))
    report.append(header)
    for r in range(5):
        report.append(f"{CLASS_NAMES[r][:9]:>9}" + "".join(f"{cm[r, c]:>9}" for c in range(5)))
    report.append("")
    report.append("precisión / recall por clase:")
    for c in range(5):
        tp = cm[c, c]
        n_real = cm[c].sum()
        n_pred = cm[:, c].sum()
        prec = tp / n_pred if n_pred else 0.0
        rec = tp / n_real if n_real else 0.0
        report.append(f"  {CLASS_NAMES[c]:>11}: prec {prec:.3f} | recall {rec:.3f} | reales {n_real}")

    return (best_state or model.state_dict()), best_acc, cm, report


async def main() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    print("Conectando al simulador...", flush=True)
    if not await bridge.is_ready():
        print("No hay simulador disponible; levanta primero el servidor de Minecraft.", flush=True)
        sys.exit(1)

    all_safe, all_risk = [], []
    for seed in TRAIN_SEEDS:
        print(f"[dataset] mina seed={seed}: generando arco interes y etiquetas...", flush=True)
        safe, risk = await build_mine_dataset(seed)
        all_safe += safe
        all_risk += risk
        print(f"   -> seguros={len(safe)} riesgo={len(risk)}", flush=True)

    X, y = balance_dataset(all_safe, all_risk)
    print(f"[dataset] total parches balanceados: {len(y)} | clases: "
          f"{np.bincount(y, minlength=5).tolist()}", flush=True)

    # Artefacto 1: dataset crudo (grids int8 + etiquetas) para reproducibilidad.
    dataset_path = MODEL_DIR / "cnn_dataset.npz"
    np.savez(dataset_path, X=X.astype(np.int8), y=y, seeds=np.array(TRAIN_SEEDS))
    print(f"Dataset guardado en {dataset_path}", flush=True)

    net_path = settings.cnn_model_path
    print("Entrenando 3D-CNN...", flush=True)
    state, best_acc, cm, report = train(X, y)
    torch.save({
        "state_dict": state,
        "meta": {
            "classes": 5,
            "groups": 8,
            "patch": PATCH,
            "val_acc": round(float(best_acc), 4),
            "trained_on": TRAIN_SEEDS,
            "n": int(len(y)),
            "histogram": np.bincount(y, minlength=5).tolist(),
            "confusion": cm.tolist(),
            "class_names": CLASS_NAMES,
        },
    }, net_path)
    print(f"Checkpoint guardado en {net_path} (val acc {best_acc:.3f})", flush=True)

    # Artefacto 2: reporte legible para la memoria/evidencia.
    report_path = MODEL_DIR / "cnn_report.txt"
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("Reporte guardado en %s" % report_path, flush=True)
    print("\n".join(report), flush=True)

    # Restaurar el mundo a la mina del usuario.
    await bridge.regen(USER_MINED_SEED)
    print("Mundo restaurado a la mina del usuario (seed %s)." % USER_MINED_SEED, flush=True)


if __name__ == "__main__":
    asyncio.run(main())