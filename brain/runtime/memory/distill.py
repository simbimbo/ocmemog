from __future__ import annotations

import json
import os
import re
from typing import Dict, Any, List

from brain.runtime.instrumentation import emit_event
from brain.runtime import state_store
from brain.runtime.memory import candidate, provenance, store
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


def _should_skip_local_distill(text: str) -> bool:
    cleaned = _normalize(text)
    if not cleaned or len(cleaned) < 24:
        return True
    if cleaned in {"ok", "okay", "done", "fixed", "working", "success", "positive feedback"}:
        return True
    return False


def _local_distill_summary(text: str) -> str:
    if _should_skip_local_distill(text):
        return ""
    prompt = (
        "Distill this experience into one concise operational summary. "
        "Prefer concrete cause/effect, decision, or reusable takeaway. "
        "Keep it under 220 characters. Return NONE if there is no meaningful takeaway.\n\n"
        f"Experience:\n{text}\n\n"
        "Summary:"
    )
    model = os.environ.get("OCMEMOG_PONDER_MODEL", "local-openai:qwen2.5-7b-instruct")
    try:
        result = inference.infer(prompt, provider_name=model)
    except Exception:
        return ""
    if result.get("status") != "ok":
        return ""
    output = str(result.get("output", "")).strip()
    output = re.sub(r"^(Summary|Sentence|Lesson):\s*", "", output, flags=re.IGNORECASE).strip()
    if not output or output.upper().startswith("NONE"):
        return ""
    return output[:240]


def _frontier_distill_summary(text: str) -> str:
    try:
        model = model_roles.get_model_for_role("memory")
        result = inference.infer(
            f"Distill this experience into a concise summary:\n\n{text}".strip(),
            provider_name=model,
        )
        if result.get("status") == "ok":
            return str(result.get("output", "")).strip()[:240]
    except Exception:
        return ""
    return ""


def _needs_frontier_refine(summary: str, source: str) -> bool:
    if not summary:
        return True
    lowered = summary.lower().strip()
    if lowered.startswith(("be ", "always ", "remember ", "good job", "be careful")):
        return True
    if len(summary) < 24:
        return True
    if len(summary) > len(source):
        return True
    if _normalize(summary) == _normalize(_heuristic_summary(source)):
        return True
    return False


def _reject_distilled_summary(summary: str, source: str) -> bool:
    lowered = _normalize(summary)
    if not lowered:
        return True
    if lowered in {"ok", "okay", "done", "fixed", "working", "positive feedback", "success", "passed"}:
        return True
    if len(lowered) < 16:
        return True
    if lowered.startswith(("good job", "be proactive", "be thorough", "always check", "always remember")):
        return True
    if source and lowered == _normalize(source):
        # In no-model environments the best available summary can be the
        # original one-line experience. Keep rejecting verbose/source-equal
        # fallbacks, but allow concise operational statements through.
        compact_source = re.sub(r"\s+", " ", str(source or "")).strip()
        if "\n" in compact_source or len(compact_source) > 120:
            return True
    return False


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
        "SELECT id, task_id, outcome, source_module, metadata_json FROM experiences ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()

    distilled: List[Dict[str, Any]] = []
    seen = set()

    for row in rows:
        source_id = _row_value(row, "id", 0)
        task_id = _row_value(row, "task_id", 1)
        content = _row_value(row, "outcome", 2) or ""
        source_module = _row_value(row, "source_module", 3)
        raw_metadata = _row_value(row, "metadata_json", 4) or "{}"
        try:
            experience_metadata = json.loads(raw_metadata) if isinstance(raw_metadata, str) else dict(raw_metadata or {})
        except Exception:
            experience_metadata = {}
        content, _ = redaction.redact_text(content)

        heuristic_summary = _heuristic_summary(content)
        summary = _local_distill_summary(content)
        if _needs_frontier_refine(summary, content):
            refined = _frontier_distill_summary(content)
            if refined:
                summary = refined

        if not summary or len(summary) > len(content):
            summary = heuristic_summary

        summary, _ = redaction.redact_text(summary)
        norm = _normalize(summary)
        if _reject_distilled_summary(summary, content):
            emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_distill_rejected", status="ok")
            continue
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

        candidate_metadata = provenance.normalize_metadata(
            {
                **experience_metadata,
                "compression_ratio": round(ratio, 3),
                "task_id": task_id,
                "source_event_id": source_id,
                "experience_reference": f"experiences:{source_id}",
                "derived_via": "distill",
                "kind": "distilled_candidate",
                "source_labels": [*(experience_metadata.get("source_labels") or []), *( [source_module] if source_module else [])],
            },
            source=source_module,
        )
        candidate_result = candidate.create_candidate(
            source_event_id=source_id,
            distilled_summary=summary,
            verification_points=verification,
            confidence_score=score,
            metadata=candidate_metadata,
        )

        distilled.append({
            "source_event_id": source_id,
            "distilled_summary": summary,
            "verification_points": verification,
            "confidence_score": score,
            "compression_ratio": round(ratio, 3),
            "candidate_id": candidate_result.get("candidate_id"),
            "duplicate": candidate_result.get("duplicate"),
            "provenance": provenance.preview_from_metadata(candidate_metadata),
        })
        emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_distill_success", status="ok")

    return distilled


def distill_artifact(artifact: Dict[str, Any]) -> List[Dict[str, Any]]:
    text = artifact.get("content_text", "")
    if not isinstance(text, str) or not text.strip():
        return []

    text, _ = redaction.redact_text(text)
    summary = _local_distill_summary(text)
    if _needs_frontier_refine(summary, text):
        refined = _frontier_distill_summary(text)
        if refined:
            summary = refined
    if not summary or len(summary) > len(text):
        summary = _heuristic_summary(text)
    summary, _ = redaction.redact_text(summary)
    if _reject_distilled_summary(summary, text):
        emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_distill_rejected", status="ok")
        return []
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

    candidate_metadata = provenance.normalize_metadata(
        {
            "compression_ratio": round(ratio, 3),
            "artifact_id": artifact.get("artifact_id"),
            "derived_via": "artifact_distill",
            "kind": "distilled_candidate",
            "source_labels": ["artifact"],
        }
    )
    candidate_result = candidate.create_candidate(
        source_event_id=0,
        distilled_summary=summary,
        verification_points=verification,
        confidence_score=score,
        metadata=candidate_metadata,
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
        "provenance": provenance.preview_from_metadata(candidate_metadata),
    }]

    candidate_metadata = provenance.normalize_metadata(
        {
            "compression_ratio": round(ratio, 3),
            "artifact_id": artifact.get("artifact_id"),
            "derived_via": "artifact_distill",
            "kind": "distilled_candidate",
            "source_labels": ["artifact"],
        }
    )
    candidate_result = candidate.create_candidate(
        source_event_id=0,
        distilled_summary=summary,
        verification_points=verification,
        confidence_score=score,
        metadata=candidate_metadata,
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
        "provenance": provenance.preview_from_metadata(candidate_metadata),
    }]

    candidate_metadata = provenance.normalize_metadata(
        {
            "compression_ratio": round(ratio, 3),
            "artifact_id": artifact.get("artifact_id"),
            "derived_via": "artifact_distill",
            "kind": "distilled_candidate",
            "source_labels": ["artifact"],
        }
    )
    candidate_result = candidate.create_candidate(
        source_event_id=0,
        distilled_summary=summary,
        verification_points=verification,
        confidence_score=score,
        metadata=candidate_metadata,
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
        "provenance": provenance.preview_from_metadata(candidate_metadata),
    }]
