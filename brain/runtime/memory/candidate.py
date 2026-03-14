from __future__ import annotations

import uuid
import json
from typing import Dict, Any

from brain.runtime.instrumentation import emit_event
from brain.runtime import state_store
from brain.runtime.memory import store
from brain.runtime.security import redaction


LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def create_candidate(
    source_event_id: int,
    distilled_summary: str,
    verification_points: list[str],
    confidence_score: float,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    summary, redacted = redaction.redact_text(distilled_summary)
    verification_lines = []
    for point in verification_points:
        clean, _ = redaction.redact_text(str(point))
        verification_lines.append(clean)

    conn = store.connect()
    row = conn.execute(
        "SELECT candidate_id FROM candidates WHERE source_event_id=? AND distilled_summary=?",
        (source_event_id, summary),
    ).fetchone()
    if row:
        conn.close()
        emit_event(LOGFILE, "brain_memory_candidate_duplicate", status="ok", source_event_id=source_event_id)
        return {"candidate_id": row[0], "duplicate": True}

    candidate_id = str(uuid.uuid4())
    verification_status = "verified" if verification_lines else "unverified"
    conn.execute(
        """
        INSERT INTO candidates (
            candidate_id, source_event_id, distilled_summary, verification_points,
            confidence_score, status, verification_status, metadata_json, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate_id,
            source_event_id,
            summary,
            "\n".join(verification_lines),
            confidence_score,
            "pending",
            verification_status,
            json.dumps(metadata or {}),
            store.SCHEMA_VERSION,
        ),
    )
    conn.execute(
        "INSERT INTO memory_events (event_type, source, details_json, schema_version) VALUES (?, ?, ?, ?)",
        (
            "candidate_created",
            str(source_event_id),
            json.dumps({"candidate_id": candidate_id, "redacted": redacted, "verification_status": verification_status}),
            store.SCHEMA_VERSION,
        ),
    )
    conn.commit()
    conn.close()
    emit_event(LOGFILE, "brain_memory_candidate_created", status="ok", source_event_id=source_event_id, redacted=redacted)
    return {"candidate_id": candidate_id, "duplicate": False}


def get_candidate(candidate_id: str) -> Dict[str, Any] | None:
    conn = store.connect()
    row = conn.execute(
        """
        SELECT candidate_id, source_event_id, distilled_summary, verification_points,
               confidence_score, status, verification_status, metadata_json
        FROM candidates
        WHERE candidate_id=?
        """,
        (candidate_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None
