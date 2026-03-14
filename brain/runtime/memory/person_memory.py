from __future__ import annotations

import json
import sqlite3
import time
from typing import Dict, List

from brain.runtime import state_store
from brain.runtime.instrumentation import emit_event

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def _connect() -> sqlite3.Connection:
    path = state_store.data_dir() / "person_memory.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS person_memory (
            person_id TEXT PRIMARY KEY,
            display_name TEXT,
            aliases_json TEXT,
            email_addresses_json TEXT,
            phone_numbers_json TEXT,
            interaction_count INTEGER DEFAULT 0,
            trust_score REAL DEFAULT 0.5,
            trust_level TEXT DEFAULT 'operator',
            expertise_tags_json TEXT,
            communication_style_json TEXT,
            last_seen TEXT,
            relationship_type TEXT,
            notes_json TEXT,
            created_at TEXT
        )
        """
    )
    _ensure_columns(conn)
    return conn


def _ensure_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(person_memory)")}
    columns = {
        "trust_level": "ALTER TABLE person_memory ADD COLUMN trust_level TEXT DEFAULT 'operator'",
        "expertise_tags_json": "ALTER TABLE person_memory ADD COLUMN expertise_tags_json TEXT",
        "communication_style_json": "ALTER TABLE person_memory ADD COLUMN communication_style_json TEXT",
        "relationship_type": "ALTER TABLE person_memory ADD COLUMN relationship_type TEXT",
        "notes_json": "ALTER TABLE person_memory ADD COLUMN notes_json TEXT",
        "created_at": "ALTER TABLE person_memory ADD COLUMN created_at TEXT",
    }
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(ddl)


def create_person(person_id: str, display_name: str = "") -> Dict[str, object]:
    conn = _connect()
    conn.execute(
        """
        INSERT OR IGNORE INTO person_memory (
            person_id, display_name, aliases_json, email_addresses_json, phone_numbers_json,
            interaction_count, trust_score, trust_level, expertise_tags_json, communication_style_json,
            last_seen, relationship_type, notes_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            person_id,
            display_name,
            json.dumps([]),
            json.dumps([]),
            json.dumps([]),
            0,
            0.5,
            "operator",
            json.dumps([]),
            json.dumps({}),
            time.strftime("%Y-%m-%d %H:%M:%S"),
            "",
            json.dumps([]),
            time.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()
    emit_event(LOGFILE, "brain_person_memory_created", status="ok", person_id=person_id)
    return get_person(person_id) or {}


def get_person(person_id: str) -> Dict[str, object] | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM person_memory WHERE person_id=?", (person_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_person(person_id: str, updates: Dict[str, object]) -> None:
    conn = _connect()
    fields = []
    values = []
    for key, value in updates.items():
        fields.append(f"{key}=?")
        values.append(value)
    if not fields:
        conn.close()
        return
    values.append(person_id)
    conn.execute(f"UPDATE person_memory SET {', '.join(fields)} WHERE person_id=?", values)
    conn.commit()
    conn.close()
    emit_event(LOGFILE, "brain_person_memory_updated", status="ok", person_id=person_id)


def _find_by_json(field: str, value: str) -> Dict[str, object] | None:
    conn = _connect()
    rows = conn.execute("SELECT * FROM person_memory").fetchall()
    conn.close()
    for row in rows:
        payload = json.loads(row[field] or "[]")
        if value in payload:
            return dict(row)
    return None


def find_person_by_email(email: str) -> Dict[str, object] | None:
    return _find_by_json("email_addresses_json", email)


def find_person_by_phone(phone: str) -> Dict[str, object] | None:
    return _find_by_json("phone_numbers_json", phone)


def list_people(limit: int = 50) -> List[Dict[str, object]]:
    conn = _connect()
    rows = conn.execute("SELECT * FROM person_memory ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]
