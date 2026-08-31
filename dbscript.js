PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE mines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    seed TEXT NOT NULL,
    spawn_x INTEGER NOT NULL DEFAULT 0,
    spawn_y INTEGER NOT NULL DEFAULT 64,
    spawn_z INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE missions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mine_id INTEGER NOT NULL REFERENCES mines(id) ON DELETE CASCADE,
    map_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','in_progress','paused','completed','canceled')),
    register_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    start_time DATETIME,
    end_time DATETIME
);
CREATE INDEX idx_missions_user   ON missions(user_id);
CREATE INDEX idx_missions_mine   ON missions(mine_id);
CREATE INDEX idx_missions_status ON missions(status);

CREATE TABLE voxeles (
    mission_id INTEGER NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    coord_x INTEGER NOT NULL,
    coord_y INTEGER NOT NULL,
    coord_z INTEGER NOT NULL,
    entropy_value REAL DEFAULT 1.0000,
    mission_step INTEGER,
    PRIMARY KEY (mission_id, coord_x, coord_y, coord_z)
);
