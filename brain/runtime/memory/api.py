from __future__ import annotations

from typing import List, Dict, Any

from brain.runtime.memory import store
from brain.runtime.instrumentation import emit_event
from brain.runtime.security import redaction


def _sanitize(text: str) -> str:
    redacted, _ = redaction.redact_text(text)
    return redacted


def record_event(event_class: str, payload: str) -> None:
    payload = _sanitize(payload)
    conn = store.connect()
    conn.execute(
        "INSERT INTO events (event_class, payload, schema_version) VALUES (?, ?, ?)",
        (event_class, payload, store.SCHEMA_VERSION),
    )
    conn.commit()
    conn.close()
    emit_event(store.state_store.reports_dir() / "brain_memory.log.jsonl", "record_event", status="ok")


def record_task(task_id: str, status: str) -> None:
    status = _sanitize(status)
    conn = store.connect()
    conn.execute(
        "INSERT INTO tasks (task_id, status, schema_version) VALUES (?, ?, ?)",
        (task_id, status, store.SCHEMA_VERSION),
    )
    conn.commit()
    conn.close()
    emit_event(store.state_store.reports_dir() / "brain_memory.log.jsonl", "record_task", status="ok")


def store_memory(memory_type: str, content: str) -> None:
    content = _sanitize(content)
    conn = store.connect()
    conn.execute(
        "INSERT INTO memories (memory_type, content, schema_version) VALUES (?, ?, ?)",
        (memory_type, content, store.SCHEMA_VERSION),
    )
    conn.commit()
    conn.close()
    emit_event(store.state_store.reports_dir() / "brain_memory.log.jsonl", "store_memory", status="ok")


def record_reinforcement(task_id: str, outcome: str, note: str) -> None:
    outcome = _sanitize(outcome)
    note = _sanitize(note)
    conn = store.connect()
    conn.execute(
        "INSERT INTO reinforcement (task_id, outcome, note, schema_version) VALUES (?, ?, ?, ?)",
        (task_id, outcome, note, store.SCHEMA_VERSION),
    )
    conn.commit()
    conn.close()
    emit_event(store.state_store.reports_dir() / "brain_memory.log.jsonl", "record_reinforcement", status="ok")


def get_recent_events(limit: int = 10) -> List[Dict[str, Any]]:
    conn = store.connect()
    rows = conn.execute(
        "SELECT id, created_at, event_class, payload FROM events ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_recent_tasks(limit: int = 10) -> List[Dict[str, Any]]:
    conn = store.connect()
    rows = conn.execute(
        "SELECT id, created_at, task_id, status FROM tasks ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_memories(limit: int = 10) -> List[Dict[str, Any]]:
    conn = store.connect()
    rows = conn.execute(
        "SELECT id, created_at, memory_type, content FROM memories ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
