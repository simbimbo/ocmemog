from __future__ import annotations

from typing import Dict, Any

from brain.runtime.instrumentation import emit_event
from brain.runtime import state_store
from brain.runtime.memory import store


def log_experience(
    task_id: str,
    outcome: str,
    confidence: float,
    reward_score: float,
    memory_reference: str,
    experience_type: str,
    source_module: str,
) -> Dict[str, Any]:
    conn = store.connect()
    row = conn.execute(
        "SELECT id FROM experiences WHERE task_id=? AND memory_reference=? AND outcome=?",
        (task_id, memory_reference, outcome),
    ).fetchone()
    if row:
        conn.close()
        emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_experience_duplicate", status="ok")
        return {"experience_id": row[0], "duplicate": True}

    cur = conn.execute(
        "INSERT INTO experiences (task_id, outcome, reward_score, confidence, memory_reference, experience_type, source_module, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (task_id, outcome, reward_score, confidence, memory_reference, experience_type, source_module, store.SCHEMA_VERSION),
    )
    conn.commit()
    conn.close()
    emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_experience_logged", status="ok")
    return {"experience_id": cur.lastrowid, "duplicate": False, "experience_type": experience_type, "source_module": source_module}


def log_task_execution(
    *,
    task_id: str,
    task_type: str,
    agent_id: str,
    tool_used: str,
    success: bool,
    duration_ms: int,
) -> Dict[str, Any]:
    outcome_payload = {
        "task_type": task_type,
        "agent_id": agent_id,
        "tool_used": tool_used,
        "success": bool(success),
        "duration_ms": duration_ms,
    }
    return log_experience(
        task_id=task_id,
        outcome=str(outcome_payload),
        confidence=1.0,
        reward_score=1.0 if success else 0.0,
        memory_reference=f"tool:{tool_used}",
        experience_type="task_execution",
        source_module="task_engine",
    )


def list_recent_experiences(limit: int = 20) -> Dict[str, int]:
    conn = store.connect()
    rows = conn.execute(
        "SELECT experience_type, COUNT(*) as count FROM experiences GROUP BY experience_type ORDER BY count DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return {row[0]: int(row[1]) for row in rows}
