import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

log = logging.getLogger("settings")

_JWT_SECRET_ENV = os.getenv("JWT_SECRET")
if _JWT_SECRET_ENV:
    jwt_secret = _JWT_SECRET_ENV
else:
    jwt_secret = secrets.token_urlsafe(48)
    log.warning(
        "JWT_SECRET no definido en el entorno; se genera un secreto aleatorio "
        "por proceso (los tokens no sobreviven a un reinicio del backend)."
    )


@dataclass(frozen=True)
class Settings:
    app_name: str = "Mine Explorer API"
    db_path: Path = Path(os.getenv("DB_PATH", BASE_DIR / "data" / "mine_explorer.db"))
    jwt_secret: str = jwt_secret
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "720"))
    mc_ws_url: str = os.getenv("MC_WS_URL", "ws://127.0.0.1:8765")
    maps_dir: Path = field(default_factory=lambda: Path(os.getenv("MAPS_DIR", BASE_DIR / "data" / "maps")))
    server_dir: Path = Path(os.getenv("SERVER_DIR", BASE_DIR.parent / "server"))
    cnn_model_path: Path = field(
        default_factory=lambda: Path(os.getenv("CNN_MODEL_PATH", BASE_DIR / "data" / "models" / "cnn_mina.pt"))
    )
    cnn_enable: bool = os.getenv("CNN_ENABLE", "1") != "0"


settings = Settings()
