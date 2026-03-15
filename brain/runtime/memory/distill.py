from __future__ import annotations

import re
from typing import Dict, Any, List

from brain.runtime.instrumentation import emit_event
from brain.runtime import state_store
from brain.runtime.memory import store, candidate
from brain.runtime.security import redaction
from brain.runtime import inference
from brain.runtime import model_roles


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _heuristic_summary(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[0][:240]


def _verification_points(text: str) -> List[str]:
    points = []
    if "verify" in text.lower():
        points.append("Verify referenced assumptions")
    if "risk" in text.lower():
        points.append("Validate risk and mitigation")
    if not points:
        points.append("Confirm key facts before promotion")
    return points[:3]


def _candidate_score(summary: str, source: str) -> float:
    if not source:
        return 0.0
    ratio = len(summary) / max(1, len(source))
    score = 1.0 - min(1.0, ratio * 0.5)
    return round(max(0.1, min(1.0, score)), 3)


def _row_value(row: Any, key: str, fallback_index: int | None = None) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except Exception:
        if fallback_index is None:
            return None
        try:
            return row[fallback_index]
        except Exception:
            return None



def distill_experiences(limit: int = 10) -> List[Dict[str, Any]]:
    emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_distill_start", status="ok")
    conn = store.connect()
    rows = conn.execute(
        "SELECT id, outcome FROM experiences ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()

    distilled: List[Dict[str, Any]] = []
    seen = set()

    for row in rows:
        source_id = _row_value(row, "id", 0)
        content = _row_value(row, "outcome", 1) or ""
        content, _ = redaction.redact_text(content)

        summary = ""
        try:
            model = model_roles.get_model_for_role("memory")
            result = inference.infer(
                f"Distill this experience into a concise summary:\n\n{content}".strip(),
                provider_name=model,
            )
            if result.get("status") == "ok":
                summary = str(result.get("output", "")).strip()
        except Exception:
            summary = ""

        if not summary or len(summary) > len(content):
            summary = _heuristic_summary(content)

        summary, _ = redaction.redact_text(summary)
        norm = _normalize(summary)
        if not norm or norm in seen:
            emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_distill_rejected", status="ok")
            continue

        seen.add(norm)
        verification = _verification_points(content)
        score = _candidate_score(summary, content)
        ratio = len(summary) / max(1, len(content))

        if score <= 0.1:
            emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_distill_rejected", status="ok")
            continue

        candidate_result = candidate.create_candidate(
            source_event_id=source_id,
            distilled_summary=summary,
            verification_points=verification,
            confidence_score=score,
            metadata={"compression_ratio": round(ratio, 3)},
        )

        distilled.append({
            "source_event_id": source_id,
            "distilled_summary": summary,
            "verification_points": verification,
            "confidence_score": score,
            "compression_ratio": round(ratio, 3),
            "candidate_id": candidate_result.get("candidate_id"),
            "duplicate": candidate_result.get("duplicate"),
        })
        emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_distill_success", status="ok")

    return distilled


def distill_artifact(artifact: Dict[str, Any]) -> List[Dict[str, Any]]:
    text = artifact.get("content_text", "")
    if not isinstance(text, str) or not text.strip():
        return []

    text, _ = redaction.redact_text(text)
    summary = _heuristic_summary(text)
    summary, _ = redaction.redact_text(summary)
    norm = _normalize(summary)
    if not norm:
        emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_distill_rejected", status="ok")
        return []

    verification = _verification_points(text)
    score = _candidate_score(summary, text)
    ratio = len(summary) / max(1, len(text))

    if score <= 0.1:
        emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_distill_rejected", status="ok")
        return []

    candidate_result = candidate.create_candidate(
        source_event_id=0,
        distilled_summary=summary,
        verification_points=verification,
        confidence_score=score,
        metadata={"compression_ratio": round(ratio, 3), "artifact_id": artifact.get("artifact_id")},
    )

    emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_distill_success", status="ok")
    return [{
        "source_event_id": 0,
        "distilled_summary": summary,
        "verification_points": verification,
        "confidence_score": score,
        "compression_ratio": round(ratio, 3),
        "candidate_id": candidate_result.get("candidate_id"),
        "duplicate": candidate_result.get("duplicate"),
    }]
