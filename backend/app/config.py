import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

log = logging.getLogger("settings")


def _load_dotenv(path: Path) -> None:
    """Carga variables KEY=VALUE del primer .env que exista (sin sobrescribir env)."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


ENV_FILE = BASE_DIR / ".env"
_load_dotenv(ENV_FILE)
_load_dotenv(BASE_DIR.parent / ".env")


def _jwt_secret_persistent() -> str:
    """JWT_SECRET de la env/.env; si no existe, se genera y se persiste en .env
    para que los tokens sigan siendo válidos tras reiniciar el backend."""
    cached = os.getenv("JWT_SECRET")
    if cached:
        return cached
    token = secrets.token_urlsafe(48)
    try:
        existing = ENV_FILE.read_text() if ENV_FILE.is_file() else ""
        with open(ENV_FILE, "a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(f"JWT_SECRET={token}\n")
        log.info("JWT_SECRET generado y persistido en %s", ENV_FILE)
    except OSError as exc:
        log.warning("No se pudo persistir JWT_SECRET en %s: %s", ENV_FILE, exc)
    return token


jwt_secret = _jwt_secret_persistent()


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
