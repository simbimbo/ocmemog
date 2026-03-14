from __future__ import annotations

import json
from typing import List, Dict, Any

from brain.runtime.memory import store
from brain.runtime.instrumentation import emit_event
from brain.runtime.security import redaction


def _sanitize(text: str) -> str:
    redacted, _ = redaction.redact_text(text)
    return redacted


def _emit(event: str) -> None:
    emit_event(store.state_store.reports_dir() / "brain_memory.log.jsonl", event, status="ok")


def record_event(event_type: str, payload: str, *, source: str | None = None) -> None:
    payload = _sanitize(payload)
    details_json = json.dumps({"payload": payload})
    conn = store.connect()
    conn.execute(
        "INSERT INTO memory_events (event_type, source, details_json, schema_version) VALUES (?, ?, ?, ?)",
        (event_type, source, details_json, store.SCHEMA_VERSION),
    )
    conn.commit()
    conn.close()
    _emit("record_event")


def record_task(task_id: str, status: str, *, source: str | None = None) -> None:
    status = _sanitize(status)
    metadata_json = json.dumps({"task_id": task_id})
    conn = store.connect()
    conn.execute(
        "INSERT INTO tasks (source, confidence, metadata_json, content, schema_version) VALUES (?, ?, ?, ?, ?)",
        (source, 1.0, metadata_json, status, store.SCHEMA_VERSION),
    )
    conn.commit()
    conn.close()
    _emit("record_task")


def store_memory(
    memory_type: str,
    content: str,
    *,
    source: str | None = None,
    metadata: Dict[str, Any] | None = None,
) -> int:
    content = _sanitize(content)
    table = memory_type.strip().lower() if memory_type else "knowledge"
    allowed = {"knowledge", "reflections", "directives", "tasks", "runbooks", "lessons"}
    if table not in allowed:
        table = "knowledge"
    conn = store.connect()
    cur = conn.execute(
        f"INSERT INTO {table} (source, confidence, metadata_json, content, schema_version) VALUES (?, ?, ?, ?, ?)",
        (source, 1.0, json.dumps(metadata or {}), content, store.SCHEMA_VERSION),
    )
    conn.commit()
    conn.close()
    _emit("store_memory")
    return int(cur.lastrowid)


def record_reinforcement(task_id: str, outcome: str, note: str, *, source_module: str | None = None) -> None:
    outcome = _sanitize(outcome)
    note = _sanitize(note)
    conn = store.connect()
    conn.execute(
        "INSERT INTO experiences (task_id, outcome, reward_score, confidence, experience_type, source_module, schema_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_id, outcome, None, 1.0, "reinforcement", source_module, store.SCHEMA_VERSION),
    )
    conn.execute(
        "INSERT INTO memory_events (event_type, source, details_json, schema_version) VALUES (?, ?, ?, ?)",
        ("reinforcement_note", source_module, json.dumps({"task_id": task_id, "note": note}), store.SCHEMA_VERSION),
    )
    conn.commit()
    conn.close()
    _emit("record_reinforcement")


def get_recent_events(limit: int = 10) -> List[Dict[str, Any]]:
    conn = store.connect()
    rows = conn.execute(
        "SELECT id, timestamp, event_type, source, details_json FROM memory_events ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_recent_tasks(limit: int = 10) -> List[Dict[str, Any]]:
    conn = store.connect()
    rows = conn.execute(
        "SELECT id, timestamp, source, confidence, metadata_json, content FROM tasks ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_memories(limit: int = 10) -> List[Dict[str, Any]]:
    conn = store.connect()
    rows = conn.execute(
        "SELECT id, timestamp, source, confidence, metadata_json, content FROM knowledge ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
