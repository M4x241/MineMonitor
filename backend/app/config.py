import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    app_name: str = "Mine Explorer API"
    db_path: Path = Path(os.getenv("DB_PATH", BASE_DIR / "data" / "mine_explorer.db"))
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-secret-change-me")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))
    mc_ws_url: str = os.getenv("MC_WS_URL", "ws://127.0.0.1:8765")
    maps_dir: Path = field(default_factory=lambda: Path(os.getenv("MAPS_DIR", BASE_DIR / "data" / "maps")))


settings = Settings()
