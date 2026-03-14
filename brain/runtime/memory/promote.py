from __future__ import annotations

import json
from typing import Dict, Any

from brain.runtime.instrumentation import emit_event
from brain.runtime import state_store
from brain.runtime.memory import store


LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def _should_promote(confidence: float, threshold: float = 0.5) -> bool:
    return confidence >= threshold


def _destination_table(summary: str) -> str:
    lowered = summary.lower()
    if "runbook" in lowered or "procedure" in lowered or "steps" in lowered:
        return "runbooks"
    if "lesson" in lowered or "postmortem" in lowered or "learned" in lowered:
        return "lessons"
    return "knowledge"


def promote_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    emit_event(LOGFILE, "brain_memory_promote_start", status="ok")
    confidence = float(candidate.get("confidence_score", 0.0))
    decision = "promote" if _should_promote(confidence) else "reject"
    candidate_id = str(candidate.get("candidate_id") or "")

    conn = store.connect()
    promotion_id = None
    destination = _destination_table(str(candidate.get("distilled_summary", "")))
    if decision == "promote":
        row = conn.execute(
            "SELECT id FROM promotions WHERE source=? AND content=?",
            (str(candidate.get("source_event_id")), candidate.get("distilled_summary", "")),
        ).fetchone()
        if not row:
            cur = conn.execute(
                """
                INSERT INTO promotions (
                    candidate_id, source, confidence, status, decision_reason,
                    metadata_json, content, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    str(candidate.get("source_event_id")),
                    confidence,
                    "promoted",
                    "confidence_threshold",
                    json.dumps(candidate.get("metadata", {})),
                    candidate.get("distilled_summary", ""),
                    store.SCHEMA_VERSION,
                ),
            )
            memory_rec = conn.execute(
                f"INSERT INTO {destination} (source, confidence, metadata_json, content, schema_version) VALUES (?, ?, ?, ?, ?)",
                (
                    str(candidate.get("source_event_id")),
                    confidence,
                    json.dumps(candidate.get("metadata", {})),
                    candidate.get("distilled_summary", ""),
                    store.SCHEMA_VERSION,
                ),
            )
            conn.execute(
                "UPDATE candidates SET status='promoted', updated_at=datetime('now') WHERE candidate_id=?",
                (candidate_id,),
            )
            conn.execute(
                "INSERT INTO memory_events (event_type, source, details_json, schema_version) VALUES (?, ?, ?, ?)",
                (
                    "candidate_promoted",
                    str(candidate.get("source_event_id")),
                    json.dumps({"candidate_id": candidate_id, "promotion_table": destination}),
                    store.SCHEMA_VERSION,
                ),
            )
            conn.commit()
            promotion_id = cur.lastrowid
            memory_id = memory_rec.lastrowid
        else:
            promotion_id = row[0]
            memory_id = None
        emit_event(LOGFILE, "brain_memory_promote_success", status="ok", destination=destination)
    else:
        conn.execute(
            "UPDATE candidates SET status='rejected', updated_at=datetime('now') WHERE candidate_id=?",
            (candidate_id,),
        )
        conn.execute(
            "INSERT INTO promotions (candidate_id, source, confidence, status, decision_reason, metadata_json, content, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                candidate_id,
                str(candidate.get("source_event_id")),
                confidence,
                "rejected",
                "below_threshold",
                json.dumps(candidate.get("metadata", {})),
                candidate.get("distilled_summary", ""),
                store.SCHEMA_VERSION,
            ),
        )
        conn.commit()
        emit_event(LOGFILE, "brain_memory_promote_rejected", status="ok")
        memory_id = None
    conn.close()

    if decision == "promote" and promotion_id is not None:
        from brain.runtime.memory import reinforcement, vector_index

        reinforcement.log_experience(
            task_id=str(candidate.get("candidate_id") or candidate.get("source_event_id") or ""),
            outcome="promoted",
            confidence=confidence,
            reward_score=confidence,
            memory_reference=f"promotion:{promotion_id}",
            experience_type="promotion",
            source_module="memory_promote",
        )
        emit_event(LOGFILE, "brain_memory_reinforcement_created", status="ok")
        if memory_id:
            vector_index.insert_memory(memory_id, candidate.get("distilled_summary", ""), confidence)

    return {"decision": decision, "confidence": confidence, "promotion_id": promotion_id, "destination": destination}


def promote_candidate_by_id(candidate_id: str) -> Dict[str, Any]:
    conn = store.connect()
    row = conn.execute(
        """
        SELECT candidate_id, source_event_id, distilled_summary, verification_points,
               confidence_score, metadata_json
        FROM candidates WHERE candidate_id=?
        """,
        (candidate_id,),
    ).fetchone()
    conn.close()
    if not row:
        emit_event(LOGFILE, "brain_memory_promote_error", status="error")
        return {"decision": "error", "reason": "candidate_not_found"}
    payload = dict(row)
    try:
        payload["metadata"] = json.loads(payload.get("metadata_json") or "{}")
    except Exception:
        payload["metadata"] = {}
    return promote_candidate(payload)
