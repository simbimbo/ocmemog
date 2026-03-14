from __future__ import annotations

from typing import Dict, Any, List

from brain.runtime.instrumentation import emit_event
from brain.runtime import state_store
from brain.runtime.memory import store


def run_integrity_check() -> Dict[str, Any]:
    emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_integrity_start", status="ok")
    conn = store.connect()
    issues: List[str] = []

    # required tables
    required = {"experiences", "knowledge", "reflections", "tasks", "directives", "promotions", "candidates", "memory_index"}
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    missing = required - tables
    if missing:
        issues.append(f"missing_tables:{','.join(sorted(missing))}")
        emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_integrity_issue", status="warn")

    # orphan candidates (source_event_id missing in experiences)
    try:
        orphan = conn.execute(
            "SELECT COUNT(*) FROM candidates WHERE source_event_id NOT IN (SELECT id FROM experiences)",
        ).fetchone()[0]
        if orphan:
            issues.append(f"orphan_candidates:{orphan}")
            emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_integrity_issue", status="warn")
    except Exception:
        pass

    # duplicate promotions
    try:
        dup = conn.execute(
            "SELECT COUNT(*) FROM promotions GROUP BY source, content HAVING COUNT(*) > 1",
        ).fetchone()
        if dup:
            issues.append("duplicate_promotions")
            emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_integrity_issue", status="warn")
    except Exception:
        pass

    # reinforcement references missing
    try:
        missing_ref = conn.execute(
            "SELECT COUNT(*) FROM experiences WHERE memory_reference IS NULL OR memory_reference = ''",
        ).fetchone()[0]
        if missing_ref:
            issues.append(f"missing_memory_reference:{missing_ref}")
            emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_integrity_issue", status="warn")
    except Exception:
        pass

    # vector index mismatch
    try:
        missing_index = conn.execute(
            "SELECT COUNT(*) FROM knowledge WHERE id NOT IN (SELECT CAST(source AS INTEGER) FROM memory_index)",
        ).fetchone()[0]
        if missing_index:
            issues.append(f"vector_missing:{missing_index}")
            emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_vector_integrity_issue", status="warn")
    except Exception:
        pass

    try:
        orphan_index = conn.execute(
            "SELECT COUNT(*) FROM memory_index WHERE CAST(source AS INTEGER) NOT IN (SELECT id FROM knowledge)",
        ).fetchone()[0]
        if orphan_index:
            issues.append(f"vector_orphan:{orphan_index}")
            emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_vector_integrity_issue", status="warn")
    except Exception:
        pass

    warning_type = ""
    warning_summary = ""
    for issue in issues:
        if issue.startswith("vector_missing"):
            warning_type = "vector_missing"
            warning_summary = "Vector index missing entries"
            break
        if issue.startswith("vector_orphan"):
            warning_type = "vector_orphan"
            warning_summary = "Vector index has orphan entries"
            break

    conn.close()
    emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_integrity_complete", status="ok")
    return {
        "issues": issues,
        "ok": len(issues) == 0,
        "warning_type": warning_type,
        "warning_summary": warning_summary,
    }
