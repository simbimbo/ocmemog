from __future__ import annotations

import json
from typing import Dict, Any

from brain.runtime.instrumentation import emit_event
from brain.runtime import state_store
from brain.runtime.memory import provenance, store
from brain.runtime import config


LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def _should_promote(confidence: float, threshold: float | None = None) -> bool:
    threshold = config.OCMEMOG_PROMOTION_THRESHOLD if threshold is None else threshold
    return confidence >= float(threshold)


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

    candidate_metadata = provenance.normalize_metadata(candidate.get("metadata", {}), source="promote")
    candidate_metadata["candidate_id"] = candidate_id
    candidate_metadata["derived_from_candidate_id"] = candidate_id
    candidate_metadata["derived_via"] = "promotion"

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
                    json.dumps(candidate_metadata, ensure_ascii=False),
                    candidate.get("distilled_summary", ""),
                    store.SCHEMA_VERSION,
                ),
            )
            memory_rec = conn.execute(
                f"INSERT INTO {destination} (source, confidence, metadata_json, content, schema_version) VALUES (?, ?, ?, ?, ?)",
                (
                    str(candidate.get("source_event_id")),
                    confidence,
                    json.dumps(candidate_metadata, ensure_ascii=False),
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
                json.dumps(candidate_metadata, ensure_ascii=False),
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

        promoted_reference = f"{destination}:{memory_id}" if memory_id else ""
        promotion_updates = {
            **candidate_metadata,
            "promotion_id": promotion_id,
            "derived_from_promotion_id": promotion_id,
        }
        if promoted_reference:
            provenance.update_memory_metadata(promoted_reference, promotion_updates)
        reinforcement.log_experience(
            task_id=str(candidate.get("candidate_id") or candidate.get("source_event_id") or ""),
            outcome="promoted",
            confidence=confidence,
            reward_score=confidence,
            memory_reference=promoted_reference or f"promotions:{promotion_id}",
            experience_type="promotion",
            source_module="memory_promote",
        )
        emit_event(LOGFILE, "brain_memory_reinforcement_created", status="ok")
        if memory_id:
            vector_index.insert_memory(memory_id, candidate.get("distilled_summary", ""), confidence)
            try:
                from brain.runtime.memory import api as memory_api

                memory_api._auto_attach_governance_candidates(promoted_reference)
            except Exception as exc:
                emit_event(LOGFILE, "brain_memory_promotion_governance_failed", status="error", error=str(exc), reference=promoted_reference)

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


def demote_memory(reference: str, reason: str = "low_confidence", new_confidence: float = 0.1) -> Dict[str, Any]:
    table, sep, raw_id = reference.partition(":")
    if not sep or not raw_id.isdigit():
        return {"ok": False, "error": "invalid_reference"}
    allowed = {"knowledge", "runbooks", "lessons", "directives", "reflections", "tasks"}
    if table not in allowed:
        return {"ok": False, "error": "unsupported_table"}
    conn = store.connect()
    row = conn.execute(
        f"SELECT confidence, content, metadata_json FROM {table} WHERE id=?",
        (int(raw_id),),
    ).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": "not_found"}
    previous = float(row[0] or 0.0)
    content = str(row[1] or "")
    metadata_json = row[2] or "{}"

    # archive into cold storage, then remove from hot table
    conn.execute(
        "INSERT INTO cold_storage (source_table, source_id, content, metadata_json, reason, schema_version) VALUES (?, ?, ?, ?, ?, ?)",
        (table, int(raw_id), content, metadata_json, reason, store.SCHEMA_VERSION),
    )
    conn.execute(
        f"DELETE FROM {table} WHERE id=?",
        (int(raw_id),),
    )
    conn.execute(
        "INSERT INTO demotions (memory_reference, previous_confidence, new_confidence, reason, schema_version) VALUES (?, ?, ?, ?, ?)",
        (reference, previous, float(new_confidence), reason, store.SCHEMA_VERSION),
    )
    conn.commit()
    conn.close()
    emit_event(LOGFILE, "brain_memory_demoted", status="ok", reference=reference)
    return {"ok": True, "reference": reference, "previous": previous, "new": float(new_confidence), "archived": True}


def demote_by_confidence(limit: int = 20, threshold: float | None = None, force: bool = False) -> Dict[str, Any]:
    threshold = config.OCMEMOG_DEMOTION_THRESHOLD if threshold is None else threshold
    tables = ("knowledge", "runbooks", "lessons", "directives", "reflections", "tasks")
    conn = store.connect()
    rows = []
    for table in tables:
        try:
            rows.extend(
                conn.execute(
                    f"SELECT '{table}' AS table_name, id, confidence FROM {table} ORDER BY confidence ASC LIMIT ?",
                    (limit,),
                ).fetchall()
            )
        except Exception:
            continue
    conn.close()
    # sort by confidence and demote below threshold
    ranked = sorted(rows, key=lambda r: float(r[2] or 0.0))
    demoted = []
    for row in ranked:
        table = row[0]
        memory_id = int(row[1])
        confidence = float(row[2] or 0.0)
        if confidence >= float(threshold) and not force:
            continue
        result = demote_memory(f"{table}:{memory_id}", reason="low_confidence", new_confidence=confidence * 0.5)
        if result.get("ok"):
            demoted.append(result)
        if len(demoted) >= limit:
            break
    return {"ok": True, "threshold": float(threshold), "demoted": demoted, "count": len(demoted)}
