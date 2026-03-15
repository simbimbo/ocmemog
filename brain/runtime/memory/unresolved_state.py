from __future__ import annotations

import sqlite3
import time
from typing import Dict, List

from brain.runtime import state_store
from brain.runtime.instrumentation import emit_event

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"

TYPES = {
    "unresolved_question",
    "paused_task",
    "interrupted_thread",
    "pending_decision",
    "incomplete_hypothesis",
}


def _connect() -> sqlite3.Connection:
    path = state_store.data_dir() / "unresolved_state.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS unresolved_state (
            state_id INTEGER PRIMARY KEY AUTOINCREMENT,
            state_type TEXT NOT NULL,
            reference TEXT,
            summary TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            resolved INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    return conn


def add_unresolved_state(state_type: str, reference: str, summary: str) -> int:
    if state_type not in TYPES:
        state_type = "unresolved_question"
    conn = _connect()
    conn.execute(
        "INSERT INTO unresolved_state (state_type, reference, summary, created_at, resolved) VALUES (?, ?, ?, ?, 0)",
        (state_type, reference, summary, time.strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid()").fetchone()
    conn.close()
    emit_event(LOGFILE, "brain_unresolved_state_added", status="ok", state_type=state_type)
    return int(row[0]) if row else 0


def list_unresolved_state(limit: int = 20) -> List[Dict[str, object]]:
    conn = _connect()
    rows = conn.execute(
        "SELECT state_id, state_type, reference, summary, created_at FROM unresolved_state WHERE resolved=0 ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def resolve_unresolved_state(state_id: int) -> bool:
    conn = _connect()
    conn.execute("UPDATE unresolved_state SET resolved=1 WHERE state_id=?", (state_id,))
    conn.commit()
    conn.close()
    emit_event(LOGFILE, "brain_unresolved_state_resolved", status="ok", state_id=state_id)
    return True


def list_unresolved_state_for_references(references: List[str], limit: int = 20) -> List[Dict[str, object]]:
    refs = [str(ref).strip() for ref in references if str(ref).strip()]
    if not refs:
        return []
    placeholders = ",".join("?" for _ in refs)
    conn = _connect()
    rows = conn.execute(
        f"SELECT state_id, state_type, reference, summary, created_at FROM unresolved_state WHERE resolved=0 AND reference IN ({placeholders}) ORDER BY created_at DESC LIMIT ?",
        (*refs, limit),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def count_unresolved_state() -> int:
    conn = _connect()
    row = conn.execute("SELECT COUNT(*) FROM unresolved_state WHERE resolved=0").fetchone()
    conn.close()
    return int(row[0]) if row else 0
