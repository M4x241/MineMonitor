import sqlite3

import aiosqlite

from .config import BASE_DIR, settings

_SCHEMA = None


def read_schema() -> str:
    global _SCHEMA
    if _SCHEMA is None:
        schema_path = BASE_DIR / "db" / "schema.sql"
        _SCHEMA = schema_path.read_text(encoding="utf-8")
    return _SCHEMA


def init_db_sync() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.maps_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.executescript(read_schema())
    conn.commit()
    conn.close()


async def get_db() -> aiosqlite.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(settings.db_path)
    conn.row_factory = aiosqlite.Row
    try:
        yield conn
    finally:
        await conn.close()


def rows_to_dicts(rows: list[aiosqlite.Row]) -> list[dict]:
    return [dict(r) for r in rows]
