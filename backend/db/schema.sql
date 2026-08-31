PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    last_login DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    seed TEXT NOT NULL,
    world_name TEXT NOT NULL DEFAULT 'mine',
    spawn_x INTEGER NOT NULL DEFAULT 0,
    spawn_y INTEGER NOT NULL DEFAULT 64,
    spawn_z INTEGER NOT NULL DEFAULT 0,
    difficulty INTEGER NOT NULL DEFAULT 1,
    description TEXT,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS missions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mine_id INTEGER NOT NULL REFERENCES mines(id) ON DELETE CASCADE,
    map_path TEXT,
    config_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','in_progress','paused','completed','canceled')),
    coverage_percent REAL,
    final_entropy REAL,
    risk_detections INTEGER,
    register_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    start_time DATETIME,
    end_time DATETIME
);
CREATE INDEX IF NOT EXISTS idx_missions_user   ON missions(user_id);
CREATE INDEX IF NOT EXISTS idx_missions_mine   ON missions(mine_id);
CREATE INDEX IF NOT EXISTS idx_missions_status ON missions(status);

CREATE TABLE IF NOT EXISTS voxeles (
    mission_id INTEGER NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    coord_x INTEGER NOT NULL,
    coord_y INTEGER NOT NULL,
    coord_z INTEGER NOT NULL,
    state INTEGER NOT NULL DEFAULT 0 CHECK (state IN (0,1,2)),
    risk_label INTEGER NOT NULL DEFAULT 0,
    confidence REAL,
    entropy_value REAL DEFAULT 1.0000,
    mission_step INTEGER,
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (mission_id, coord_x, coord_y, coord_z)
);
