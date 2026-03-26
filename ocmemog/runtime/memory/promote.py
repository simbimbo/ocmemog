from __future__ import annotations

import json
from typing import Any, Dict

from ocmemog.runtime.instrumentation import emit_event
from ocmemog.runtime import config, state_store
from ocmemog.runtime.memory import provenance, store

LOGFILE = state_store.report_log_path()

_PREFERENCE_PATTERNS = (
    "prefer",
    "preference",
    "favorite",
    "favourite",
    "likes",
    "like ",
    "loves",
    "enjoys",
    "dislikes",
    "hate ",
    "avoids",
)
_IDENTITY_PATTERNS = (
    "my name is",
    "i am ",
    "i'm ",
    "my pronouns are",
    "i live in",
    "we live in",
    "i work at",
    "we work at",
    "i study at",
    "we study at",
    "my timezone is",
    "my time zone is",
    "my email is",
    "my phone number is",
    "my birthday is",
    "allergic to",
)


def _should_promote(confidence: float, threshold: float | None = None) -> bool:
    threshold = config.OCMEMOG_PROMOTION_THRESHOLD if threshold is None else threshold
    return confidence >= float(threshold)


def _destination_table(summary: str) -> str:
    lowered = summary.lower()
    if "runbook" in lowered or "procedure" in lowered or "steps" in lowered:
        return "runbooks"
    if "lesson" in lowered or "postmortem" in lowered or "learned" in lowered:
        return "lessons"
    if any(pattern in lowered for pattern in _PREFERENCE_PATTERNS):
        return "preferences"
    if any(pattern in lowered for pattern in _IDENTITY_PATTERNS):
        return "identity"
    return "knowledge"


def _promotion_explanation(*, decision: str, destination: str, confidence: float, threshold: float, summary: str) -> Dict[str, Any]:
    if decision == "promote":
        short = f"Promoted to {destination} because confidence {confidence:.2f} met threshold {threshold:.2f}."
        reason = "confidence_threshold"
    else:
        short = f"Rejected because confidence {confidence:.2f} was below threshold {threshold:.2f}."
        reason = "below_threshold"
    return {
        "short": short,
        "reason": reason,
        "destination": destination,
        "confidence": round(confidence, 3),
        "threshold": round(threshold, 3),
        "summary_preview": summary[:160],
    }


def promote_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    from ocmemog.runtime.memory import api, reinforcement, vector_index

    emit_event(LOGFILE, "brain_memory_promote_start", status="ok")
    confidence = float(candidate.get("confidence_score", 0.0))
    threshold = float(config.OCMEMOG_PROMOTION_THRESHOLD)
    decision = "promote" if _should_promote(confidence, threshold=threshold) else "reject"
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
            vector_index.insert_memory(memory_id, candidate.get("distilled_summary", ""), confidence, source_type=destination)
            try:
                api._auto_attach_governance_candidates(promoted_reference)
            except Exception as exc:
                emit_event(
                    LOGFILE,
                    "brain_memory_promotion_governance_failed",
                    status="error",
                    error=str(exc),
                    reference=promoted_reference,
                )

    return {
        "decision": decision,
        "confidence": confidence,
        "promotion_id": promotion_id,
        "destination": destination,
        "explanation": _promotion_explanation(
            decision=decision,
            destination=destination,
            confidence=confidence,
            threshold=threshold,
            summary=str(candidate.get("distilled_summary", "") or ""),
        ),
    }


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
    allowed = set(store.MEMORY_TABLES)
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
    tables = tuple(store.MEMORY_TABLES)
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
