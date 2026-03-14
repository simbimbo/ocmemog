from __future__ import annotations

import sqlite3
import time
from typing import Dict, List

from brain.runtime import state_store
from brain.runtime.instrumentation import emit_event
from brain.runtime.memory import person_memory

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def _connect() -> sqlite3.Connection:
    path = state_store.data_dir() / "interaction_memory.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS interaction_memory (
            interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id TEXT,
            timestamp TEXT,
            channel TEXT,
            thread_id TEXT,
            sentiment TEXT,
            outcome TEXT
        )
        """
    )
    return conn


def record_interaction(person_id: str, channel: str, thread_id: str, sentiment: str, outcome: str) -> None:
    conn = _connect()
    conn.execute(
        "INSERT INTO interaction_memory (person_id, timestamp, channel, thread_id, sentiment, outcome) VALUES (?, ?, ?, ?, ?, ?)",
        (person_id, time.strftime("%Y-%m-%d %H:%M:%S"), channel, thread_id, sentiment, outcome[:80]),
    )
    conn.commit()
    conn.close()
    person_memory.update_person(
        person_id,
        {"interaction_count": (person_memory.get_person(person_id) or {}).get("interaction_count", 0) + 1, "last_seen": time.strftime("%Y-%m-%d %H:%M:%S")},
    )
    emit_event(LOGFILE, "brain_person_interaction_recorded", status="ok", person_id=person_id)


def get_recent_interactions(person_id: str, limit: int = 10) -> List[Dict[str, object]]:
    conn = _connect()
    rows = conn.execute(
        "SELECT timestamp, channel, thread_id, sentiment, outcome FROM interaction_memory WHERE person_id=? ORDER BY timestamp DESC LIMIT ?",
        (person_id, limit),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
