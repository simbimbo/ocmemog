from __future__ import annotations

import uuid
import json
import re
from difflib import SequenceMatcher
from typing import Dict, Any

from brain.runtime.instrumentation import emit_event
from brain.runtime import state_store
from brain.runtime.memory import provenance, store
from ocmemog.runtime.security import redaction


LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"
_NEAR_DUPLICATE_SIMILARITY = 0.85


def _normalize_summary(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", _normalize_summary(text))}


def _summary_similarity(left: str, right: str) -> float:
    left_tokens = _tokenize(left)
    right_tokens = _tokenize(right)
    token_similarity = 0.0
    if left_tokens and right_tokens:
        overlap = len(left_tokens & right_tokens)
        union = len(left_tokens | right_tokens)
        token_similarity = overlap / max(1, union)
    sequence_similarity = SequenceMatcher(None, _normalize_summary(left), _normalize_summary(right)).ratio()
    return max(token_similarity, sequence_similarity)


def _ranges_overlap(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    if str(left.get("path") or "") != str(right.get("path") or ""):
        return False

    def _as_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except Exception:
            return None

    left_start = _as_int(left.get("start_line"))
    left_end = _as_int(left.get("end_line")) or left_start
    right_start = _as_int(right.get("start_line"))
    right_end = _as_int(right.get("end_line")) or right_start

    if left_start is None and right_start is None:
        return True
    if left_start is None or right_start is None:
        return False
    return max(left_start, right_start) <= min(left_end or left_start, right_end or right_start)


def _shares_provenance_anchor(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    left_meta = provenance.normalize_metadata(left)
    right_meta = provenance.normalize_metadata(right)
    left_prov = left_meta.get("provenance") if isinstance(left_meta.get("provenance"), dict) else {}
    right_prov = right_meta.get("provenance") if isinstance(right_meta.get("provenance"), dict) else {}

    left_conv = left_prov.get("conversation") if isinstance(left_prov.get("conversation"), dict) else {}
    right_conv = right_prov.get("conversation") if isinstance(right_prov.get("conversation"), dict) else {}
    if left_conv.get("message_id") and left_conv.get("message_id") == right_conv.get("message_id"):
        return True

    left_transcript = left_prov.get("transcript_anchor") if isinstance(left_prov.get("transcript_anchor"), dict) else {}
    right_transcript = right_prov.get("transcript_anchor") if isinstance(right_prov.get("transcript_anchor"), dict) else {}
    if left_transcript.get("path") and right_transcript.get("path") and _ranges_overlap(left_transcript, right_transcript):
        return True

    left_refs = {str(item) for item in left_prov.get("source_references") or [] if str(item).strip()}
    right_refs = {str(item) for item in right_prov.get("source_references") or [] if str(item).strip()}
    return bool(left_refs & right_refs)


def _find_near_duplicate_candidate(conn, source_event_id: int, summary: str, metadata: Dict[str, Any]) -> str | None:
    rows = conn.execute(
        """
        SELECT candidate_id, distilled_summary, metadata_json
        FROM candidates
        WHERE source_event_id != ?
        ORDER BY created_at DESC, candidate_id DESC
        LIMIT 250
        """,
        (source_event_id,),
    ).fetchall()
    normalized_summary = _normalize_summary(summary)
    for row in rows:
        existing_summary = str(row["distilled_summary"] if isinstance(row, dict) else row[1] or "")
        similarity = _summary_similarity(normalized_summary, existing_summary)
        if similarity < _NEAR_DUPLICATE_SIMILARITY:
            continue
        try:
            existing_metadata = json.loads(row["metadata_json"] if isinstance(row, dict) else row[2] or "{}")
        except Exception:
            existing_metadata = {}
        if _shares_provenance_anchor(metadata, existing_metadata):
            return str(row["candidate_id"] if isinstance(row, dict) else row[0])
    return None


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

    normalized_metadata = provenance.normalize_metadata(metadata, source="candidate")

    conn = store.connect()
    exact_row = conn.execute(
        "SELECT candidate_id FROM candidates WHERE source_event_id=? AND distilled_summary=?",
        (source_event_id, summary),
    ).fetchone()
    if exact_row:
        conn.close()
        emit_event(LOGFILE, "brain_memory_candidate_duplicate", status="ok", source_event_id=source_event_id)
        return {"candidate_id": exact_row[0], "duplicate": True}

    near_duplicate_id = _find_near_duplicate_candidate(conn, source_event_id, summary, normalized_metadata)
    if near_duplicate_id:
        conn.close()
        emit_event(LOGFILE, "brain_memory_candidate_duplicate", status="ok", source_event_id=source_event_id, duplicate_kind="near")
        return {"candidate_id": near_duplicate_id, "duplicate": True}

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
            json.dumps(normalized_metadata, ensure_ascii=False),
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
