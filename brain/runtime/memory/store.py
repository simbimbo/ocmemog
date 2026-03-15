from __future__ import annotations

import sqlite3
import queue
import threading
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

CREATE TABLE IF NOT EXISTS conversation_turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  conversation_id TEXT,
  session_id TEXT,
  thread_id TEXT,
  message_id TEXT,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  transcript_path TEXT,
  transcript_offset INTEGER,
  transcript_end_offset INTEGER,
  source TEXT,
  metadata_json TEXT DEFAULT '{}',
  schema_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_turns_conversation ON conversation_turns(conversation_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_turns_session ON conversation_turns(session_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_turns_thread ON conversation_turns(thread_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_turns_message ON conversation_turns(message_id);

CREATE TABLE IF NOT EXISTS conversation_checkpoints (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  conversation_id TEXT,
  session_id TEXT,
  thread_id TEXT,
  turn_start_id INTEGER,
  turn_end_id INTEGER,
  checkpoint_kind TEXT NOT NULL DEFAULT 'manual',
  summary TEXT NOT NULL,
  latest_user_ask TEXT,
  last_assistant_commitment TEXT,
  open_loops_json TEXT DEFAULT '[]',
  pending_actions_json TEXT DEFAULT '[]',
  metadata_json TEXT DEFAULT '{}',
  schema_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_checkpoints_conversation ON conversation_checkpoints(conversation_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_checkpoints_session ON conversation_checkpoints(session_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_checkpoints_thread ON conversation_checkpoints(thread_id, id DESC);

CREATE TABLE IF NOT EXISTS conversation_state (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope_type TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  conversation_id TEXT,
  session_id TEXT,
  thread_id TEXT,
  latest_user_turn_id INTEGER,
  latest_assistant_turn_id INTEGER,
  latest_user_ask TEXT,
  last_assistant_commitment TEXT,
  open_loops_json TEXT DEFAULT '[]',
  pending_actions_json TEXT DEFAULT '[]',
  unresolved_state_json TEXT DEFAULT '[]',
  latest_checkpoint_id INTEGER,
  metadata_json TEXT DEFAULT '{}',
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  schema_version TEXT NOT NULL,
  UNIQUE(scope_type, scope_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_state_conversation ON conversation_state(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversation_state_session ON conversation_state(session_id);
CREATE INDEX IF NOT EXISTS idx_conversation_state_thread ON conversation_state(thread_id);
"""

_WRITE_QUEUE: "queue.Queue[tuple]" = queue.Queue()
_WRITE_LOCK = threading.Lock()
_WRITE_WORKER_STARTED = False


def _write_worker() -> None:
    while True:
        fn, event, container = _WRITE_QUEUE.get()
        try:
            container["result"] = fn()
        except Exception as exc:  # pragma: no cover
            container["error"] = exc
        finally:
            event.set()
            _WRITE_QUEUE.task_done()


def _ensure_write_worker() -> None:
    global _WRITE_WORKER_STARTED
    with _WRITE_LOCK:
        if _WRITE_WORKER_STARTED:
            return
        thread = threading.Thread(target=_write_worker, daemon=True)
        thread.start()
        _WRITE_WORKER_STARTED = True


def submit_write(fn, timeout: float = 30.0):
    _ensure_write_worker()
    event = threading.Event()
    container: dict = {}
    _WRITE_QUEUE.put((fn, event, container))
    if not event.wait(timeout=timeout):
        raise TimeoutError("write queue timeout")
    if "error" in container:
        raise container["error"]
    return container.get("result")


def db_path() -> Path:
    return state_store.memory_db_path()


_SCHEMA_READY = False


def connect(*, ensure_schema: bool = True) -> sqlite3.Connection:
    global _SCHEMA_READY
    if ensure_schema and not _SCHEMA_READY:
        init_db()
        _SCHEMA_READY = True
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-20000")
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
    _ensure_column(conn, "conversation_turns", "conversation_id", "TEXT")
    _ensure_column(conn, "conversation_turns", "session_id", "TEXT")
    _ensure_column(conn, "conversation_turns", "thread_id", "TEXT")
    _ensure_column(conn, "conversation_turns", "message_id", "TEXT")
    _ensure_column(conn, "conversation_turns", "role", "TEXT NOT NULL DEFAULT 'unknown'")
    _ensure_column(conn, "conversation_turns", "content", "TEXT")
    _ensure_column(conn, "conversation_turns", "transcript_path", "TEXT")
    _ensure_column(conn, "conversation_turns", "transcript_offset", "INTEGER")
    _ensure_column(conn, "conversation_turns", "transcript_end_offset", "INTEGER")
    _ensure_column(conn, "conversation_turns", "source", "TEXT")
    _ensure_column(conn, "conversation_turns", "metadata_json", "TEXT DEFAULT '{}'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_turns_conversation ON conversation_turns(conversation_id, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_turns_session ON conversation_turns(session_id, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_turns_thread ON conversation_turns(thread_id, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_turns_message ON conversation_turns(message_id)")
    _ensure_column(conn, "conversation_checkpoints", "conversation_id", "TEXT")
    _ensure_column(conn, "conversation_checkpoints", "session_id", "TEXT")
    _ensure_column(conn, "conversation_checkpoints", "thread_id", "TEXT")
    _ensure_column(conn, "conversation_checkpoints", "turn_start_id", "INTEGER")
    _ensure_column(conn, "conversation_checkpoints", "turn_end_id", "INTEGER")
    _ensure_column(conn, "conversation_checkpoints", "checkpoint_kind", "TEXT NOT NULL DEFAULT 'manual'")
    _ensure_column(conn, "conversation_checkpoints", "summary", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "conversation_checkpoints", "latest_user_ask", "TEXT")
    _ensure_column(conn, "conversation_checkpoints", "last_assistant_commitment", "TEXT")
    _ensure_column(conn, "conversation_checkpoints", "open_loops_json", "TEXT DEFAULT '[]'")
    _ensure_column(conn, "conversation_checkpoints", "pending_actions_json", "TEXT DEFAULT '[]'")
    _ensure_column(conn, "conversation_checkpoints", "metadata_json", "TEXT DEFAULT '{}'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_checkpoints_conversation ON conversation_checkpoints(conversation_id, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_checkpoints_session ON conversation_checkpoints(session_id, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_checkpoints_thread ON conversation_checkpoints(thread_id, id DESC)")
    _ensure_column(conn, "conversation_state", "scope_type", "TEXT")
    _ensure_column(conn, "conversation_state", "scope_id", "TEXT")
    _ensure_column(conn, "conversation_state", "conversation_id", "TEXT")
    _ensure_column(conn, "conversation_state", "session_id", "TEXT")
    _ensure_column(conn, "conversation_state", "thread_id", "TEXT")
    _ensure_column(conn, "conversation_state", "latest_user_turn_id", "INTEGER")
    _ensure_column(conn, "conversation_state", "latest_assistant_turn_id", "INTEGER")
    _ensure_column(conn, "conversation_state", "latest_user_ask", "TEXT")
    _ensure_column(conn, "conversation_state", "last_assistant_commitment", "TEXT")
    _ensure_column(conn, "conversation_state", "open_loops_json", "TEXT DEFAULT '[]'")
    _ensure_column(conn, "conversation_state", "pending_actions_json", "TEXT DEFAULT '[]'")
    _ensure_column(conn, "conversation_state", "unresolved_state_json", "TEXT DEFAULT '[]'")
    _ensure_column(conn, "conversation_state", "latest_checkpoint_id", "INTEGER")
    _ensure_column(conn, "conversation_state", "metadata_json", "TEXT DEFAULT '{}'")
    _ensure_column(conn, "conversation_state", "updated_at", "TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_state_scope ON conversation_state(scope_type, scope_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_state_conversation ON conversation_state(conversation_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_state_session ON conversation_state(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_state_thread ON conversation_state(thread_id)")
    conn.commit()
    conn.close()
