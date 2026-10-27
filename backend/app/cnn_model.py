"""3D-CNN de segmentación de riesgo del terreno (Sprint 3-4 del perfil).

Codifica un parche de vóxeles 7x7x7 (one-hot por grupo de material) alrededor de
un bloque y predice la clase de riesgo del vóxel central. Sustituye la heurística
estática material->riesgo por un modelo entrenado con la verdad de terreno
(ground_truth) generada por le plugin.

Clases de salida (mismo orden que MineGenerator.riskLabel -> etiquetas de riesgo):
    0 = seguro     1 = grieta      2 = escombro suelto
    3 = obstrucción/derrubio      4 = lava / magma
"""
import numpy as np
import torch
import torch.nn as nn

PATCH = 7
NUM_CLASSES = 5

# Mapeo material -> grupo (8 canales; el 7 es "desconocido/piedra por defecto").
MATERIAL_GROUPS = {
    "AIR": 0, "CAVE_AIR": 0, "VOID_AIR": 0, "WATER": 0,
    "STONE": 1,
    "COBBLESTONE": 2, "DEEPSLATE": 2,
    "ANDESITE": 3, "DIORITE": 3, "GRANITE": 3, "COBBLED_DEEPSLATE": 3, "TUFF": 3,
    "CRACKED_STONE_BRICKS": 4,
    "GRAVEL": 5, "SAND": 5, "DIRT": 5,
    "LAVA": 6, "MAGMA_BLOCK": 6,
}
NUM_GROUPS = 8

# Referencia grupo -> clase de riesgo (para el dataset cuando solo hay material).
RISK_BY_GROUP = {
    0: 0, 1: 0, 2: 0, 3: 3, 4: 1, 5: 2, 6: 4, 7: 0,
}


def group_for_material(material: str) -> int:
    return MATERIAL_GROUPS.get((material or "STONE").upper(), 7)


def _build_index(blocks):
    """Construye un dict por clave (x,y,z) para lookup O(1)."""
    idx = {}
    for b in blocks:
        idx[(b["x"], b["y"], b["z"])] = b.get("material", "STONE")
    return idx


def build_grid(blocks, cx: int, cy: int, cz: int, d: int = PATCH) -> np.ndarray:
    """Grid (d,d,d) de índices de grupo alrededor del bloque central (cx,cy,cz).

    Acepta lista o dict indexado por (x,y,z). O(N) pasa a O(d^3).
    """
    half = d // 2
    grid = np.full((d, d, d), 7, dtype=np.int64)
    # intentar indexar
    if isinstance(blocks, dict):
        idx = blocks
    else:
        idx = _build_index(blocks)
    for dx in range(-half, half + 1):
        for dy in range(-half, half + 1):
            for dz in range(-half, half + 1):
                m = idx.get((cx + dx, cy + dy, cz + dz))
                if m is not None:
                    ix = dx + half; iy = dy + half; iz = dz + half
                    grid[ix, iy, iz] = group_for_material(m)
    return grid


class RiskCnn3D(nn.Module):
    """Red pequeña: 2 conv3d + pool adaptativo + MLP (corre en CPU)."""

    def __init__(self, num_groups: int = NUM_GROUPS, classes: int = NUM_CLASSES):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv3d(num_groups, 16, 3, padding=1),
            nn.BatchNorm3d(16),
            nn.ReLU(inplace=True),
            nn.Conv3d(16, 32, 3, padding=1),
            nn.BatchNorm3d(32),
            nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool3d(3),
            nn.Flatten(),
            nn.Linear(32 * 27, 64),
            nn.ReLU(inplace=True),
            nn.Dropout(0.3),
            nn.Linear(64, classes),
        )

    def forward(self, x):
        return self.net(x)


def encode_patch(group_grid: np.ndarray) -> np.ndarray:
    """Grid (d,d,d) de grupos -> tensor one-hot (NUM_GROUPS,d,d,d) float32."""
    d = group_grid.shape[0]
    flat = np.clip(group_grid.reshape(-1), 0, NUM_GROUPS - 1)
    one = np.zeros((NUM_GROUPS, d * d * d), dtype=np.float32)
    one[flat, np.arange(d * d * d)] = 1.0
    return one.reshape(NUM_GROUPS, d, d, d)


class RiskPredictor:
    """Carga el checkpoint entrenado y predice por lotes."""

    def __init__(self, path):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = RiskCnn3D()
        ckpt = torch.load(path, map_location=self.device, weights_only=False)
        self.model.load_state_dict(ckpt["state_dict"])
        self.model.eval()
        self.model.to(self.device)
        self.meta = ckpt.get("meta", {})

    @torch.inference_mode()
    def predict_grids(self, grids, batch_size: int = 512):
        """grids: lista de grids (d,d,d) -> (labels array, confidences array)."""
        if not grids:
            return np.array([], dtype=np.int64), np.array([], dtype=np.float32)
        labels = np.empty(len(grids), dtype=np.int64)
        confs = np.empty(len(grids), dtype=np.float32)
        for start in range(0, len(grids), batch_size):
            batch = grids[start:start + batch_size]
            X = torch.from_numpy(np.stack([encode_patch(g) for g in batch])).to(self.device)
            logits = self.model(X)
            probs = torch.softmax(logits, dim=1)
            maxp, argmax = probs.max(dim=1)
            labels[start:start + len(batch)] = argmax.cpu().numpy()
            confs[start:start + len(batch)] = maxp.cpu().numpy()
        return labels, confs