from __future__ import annotations

import sqlite3
from pathlib import Path
from brain.runtime import state_store

SCHEMA_VERSION = "v1"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memory_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  event_type TEXT NOT NULL,
  source TEXT,
  details_json TEXT DEFAULT '{}',
  schema_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS environment_cognition (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  source TEXT,
  confidence REAL NOT NULL DEFAULT 1.0,
  metadata_json TEXT DEFAULT '{}',
  content TEXT NOT NULL,
  schema_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  task_id TEXT,
  outcome TEXT,
  reward_score REAL,
  confidence REAL NOT NULL DEFAULT 1.0,
  memory_reference TEXT,
  experience_type TEXT,
  source_module TEXT,
  schema_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS directives (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  source TEXT,
  confidence REAL NOT NULL DEFAULT 1.0,
  metadata_json TEXT DEFAULT '{}',
  content TEXT NOT NULL,
  schema_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
  candidate_id TEXT PRIMARY KEY,
  source_event_id INTEGER,
  distilled_summary TEXT,
  verification_points TEXT,
  confidence_score REAL,
  status TEXT NOT NULL DEFAULT 'pending',
  verification_status TEXT NOT NULL DEFAULT 'unverified',
  metadata_json TEXT DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  schema_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS promotions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  candidate_id TEXT,
  source TEXT,
  confidence REAL NOT NULL DEFAULT 1.0,
  status TEXT NOT NULL DEFAULT 'promoted',
  decision_reason TEXT,
  metadata_json TEXT DEFAULT '{}',
  content TEXT NOT NULL,
  schema_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS demotions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  memory_reference TEXT NOT NULL,
  previous_confidence REAL,
  new_confidence REAL,
  reason TEXT,
  schema_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cold_storage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  archived_at TEXT NOT NULL DEFAULT (datetime('now')),
  source_table TEXT NOT NULL,
  source_id INTEGER NOT NULL,
  content TEXT NOT NULL,
  metadata_json TEXT DEFAULT '{}',
  reason TEXT,
  schema_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  source TEXT,
  confidence REAL NOT NULL DEFAULT 1.0,
  metadata_json TEXT DEFAULT '{}',
  content TEXT NOT NULL,
  schema_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runbooks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  source TEXT,
  confidence REAL NOT NULL DEFAULT 1.0,
  metadata_json TEXT DEFAULT '{}',
  content TEXT NOT NULL,
  schema_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lessons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  source TEXT,
  confidence REAL NOT NULL DEFAULT 1.0,
  metadata_json TEXT DEFAULT '{}',
  content TEXT NOT NULL,
  schema_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reflections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  source TEXT,
  confidence REAL NOT NULL DEFAULT 1.0,
  metadata_json TEXT DEFAULT '{}',
  content TEXT NOT NULL,
  schema_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  source TEXT,
  confidence REAL NOT NULL DEFAULT 1.0,
  metadata_json TEXT DEFAULT '{}',
  content TEXT NOT NULL,
  schema_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_index (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  source TEXT,
  confidence REAL NOT NULL DEFAULT 1.0,
  metadata_json TEXT DEFAULT '{}',
  content TEXT NOT NULL,
  schema_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vector_embeddings (
  id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  embedding TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id TEXT PRIMARY KEY,
  artifact_type TEXT,
  source_path TEXT,
  content_hash TEXT,
  metadata TEXT,
  created_at TIMESTAMP DEFAULT (datetime('now'))
);
"""


def db_path() -> Path:
    return state_store.memory_db_path()


def connect(*, ensure_schema: bool = True) -> sqlite3.Connection:
    if ensure_schema:
        init_db()
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db() -> None:
    conn = connect(ensure_schema=False)
    conn.executescript(SCHEMA_SQL)
    _ensure_column(conn, "experiences", "experience_type", "TEXT")
    _ensure_column(conn, "experiences", "source_module", "TEXT")
    _ensure_column(conn, "candidates", "status", "TEXT NOT NULL DEFAULT 'pending'")
    _ensure_column(conn, "candidates", "verification_status", "TEXT NOT NULL DEFAULT 'unverified'")
    _ensure_column(conn, "candidates", "created_at", "TEXT")
    _ensure_column(conn, "candidates", "updated_at", "TEXT")
    conn.execute("UPDATE candidates SET created_at=datetime('now') WHERE created_at IS NULL")
    conn.execute("UPDATE candidates SET updated_at=datetime('now') WHERE updated_at IS NULL")
    _ensure_column(conn, "promotions", "candidate_id", "TEXT")
    _ensure_column(conn, "promotions", "status", "TEXT NOT NULL DEFAULT 'promoted'")
    _ensure_column(conn, "promotions", "decision_reason", "TEXT")
    conn.commit()
    conn.close()
