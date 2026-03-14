from __future__ import annotations

from typing import Dict, Any, List

from brain.runtime.instrumentation import emit_event
from brain.runtime import state_store
from brain.runtime.memory import store


EMBED_TABLES = ("knowledge", "runbooks", "lessons")


def run_integrity_check() -> Dict[str, Any]:
    emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_integrity_start", status="ok")
    conn = store.connect()
    issues: List[str] = []

    # required tables
    required = {
        "experiences",
        "knowledge",
        "reflections",
        "tasks",
        "directives",
        "promotions",
        "candidates",
        "memory_index",
        "vector_embeddings",
        "runbooks",
        "lessons",
    }
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

    # vector index mismatch (knowledge/runbooks/lessons vs vector_embeddings)
    missing_vectors = 0
    orphan_vectors = 0
    try:
        for table in EMBED_TABLES:
            missing_vectors += conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE id NOT IN (SELECT CAST(source_id AS INTEGER) FROM vector_embeddings WHERE source_type=?)",
                (table,),
            ).fetchone()[0]
    except Exception:
        pass

    try:
        for table in EMBED_TABLES:
            orphan_vectors += conn.execute(
                "SELECT COUNT(*) FROM vector_embeddings WHERE source_type=? AND CAST(source_id AS INTEGER) NOT IN (SELECT id FROM %s)"
                % table,
                (table,),
            ).fetchone()[0]
    except Exception:
        pass

    if missing_vectors:
        issues.append(f"vector_missing:{missing_vectors}")
        emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_vector_integrity_issue", status="warn")

    if orphan_vectors:
        issues.append(f"vector_orphan:{orphan_vectors}")
        emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_vector_integrity_issue", status="warn")

    warning_type = ""
    warning_summary = ""
    for issue in issues:
        if issue.startswith("vector_missing"):
            warning_type = "vector_missing"
            warning_summary = "Vector embeddings missing entries"
            break
        if issue.startswith("vector_orphan"):
            warning_type = "vector_orphan"
            warning_summary = "Vector embeddings have orphan entries"
            break

    conn.close()
    emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_integrity_complete", status="ok")
    return {
        "issues": issues,
        "ok": len(issues) == 0,
        "warning_type": warning_type,
        "warning_summary": warning_summary,
    }
