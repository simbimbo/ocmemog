from __future__ import annotations

import json
import os
import re
import threading
from queue import Queue
from typing import List, Dict, Any, Optional

from ocmemog.runtime import inference
from ocmemog.runtime.instrumentation import emit_event
from . import provenance, store
from ocmemog.runtime.security import redaction

_SUPERSESSION_SUMMARY_CACHE: Dict[str, str] = {}

_REVIEW_KIND_METADATA: Dict[str, Dict[str, str]] = {
    "duplicate_candidate": {
        "relationship": "duplicate_of",
        "label": "Duplicate candidate",
        "approve_label": "Approve duplicate merge",
        "reject_label": "Reject duplicate merge",
    },
    "contradiction_candidate": {
        "relationship": "contradicts",
        "label": "Contradiction candidate",
        "approve_label": "Mark as contradiction",
        "reject_label": "Dismiss contradiction",
    },
    "supersession_recommendation": {
        "relationship": "supersedes",
        "label": "Supersession recommendation",
        "approve_label": "Approve supersession",
        "reject_label": "Dismiss supersession",
    },
}


def _sanitize(text: str) -> str:
    redacted, _ = redaction.redact_text(text)
    return redacted


def _run_with_timeout(fn, timeout_s: float, default: Any) -> Any:
    result_queue: Queue[tuple[str, Any]] = Queue(maxsize=1)

    def _target() -> None:
        try:
            result_queue.put(("ok", fn()))
        except Exception as exc:
            result_queue.put(("error", exc))

    worker = threading.Thread(target=_target, name="ocmemog-governance-summary", daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive() or result_queue.empty():
        return default
    status, payload = result_queue.get_nowait()
    if status != "ok":
        return default
    return payload


def _parse_memory_reference(reference: str) -> tuple[str, str] | None:
    if ":" not in str(reference or ""):
        return None
    table, identifier = str(reference).split(":", 1)
    table = table.strip()
    identifier = identifier.strip()
    if not table or not identifier:
        return None
    try:
        int(identifier)
    except Exception:
        return None
    return table, identifier


def _auto_attach_model_hints_enabled() -> bool:
    return os.environ.get("OCMEMOG_AUTO_ATTACH_GOVERNANCE_USE_MODEL_HINTS", "true").strip().lower() in {"1", "true", "yes"}


def _auto_attach_model_hint_budget() -> int:
    raw = os.environ.get("OCMEMOG_AUTO_ATTACH_GOVERNANCE_MODEL_HINT_BUDGET", "2")
    try:
        return max(0, int(raw or 0))
    except Exception:
        return 2


def _auto_attach_min_tokens() -> int:
    raw = os.environ.get("OCMEMOG_AUTO_ATTACH_GOVERNANCE_MIN_TOKENS", "4")
    try:
        return max(2, int(raw or 0))
    except Exception:
        return 4


def _governance_candidates_significant(content: str) -> bool:
    tokens = set(_tokenize(content))
    if len(tokens) >= _auto_attach_min_tokens():
        return True
    if _extract_literals(content):
        return True
    return False


def _emit(event: str) -> None:
    emit_event(store.state_store.report_log_path(), event, status="ok")


def record_event(event_type: str, payload: str, *, source: str | None = None) -> None:
    payload = _sanitize(payload)
    details_json = json.dumps({"payload": payload})
    def _write() -> None:
        conn = store.connect()
        try:
            conn.execute(
                "INSERT INTO memory_events (event_type, source, details_json, schema_version) VALUES (?, ?, ?, ?)",
                (event_type, source, details_json, store.SCHEMA_VERSION),
            )
            conn.commit()
        finally:
            conn.close()

    store.submit_write(_write, timeout=30.0)
    _emit("record_event")


def record_task(task_id: str, status: str, *, source: str | None = None) -> None:
    status = _sanitize(status)
    metadata_json = json.dumps({"task_id": task_id})
    def _write() -> None:
        conn = store.connect()
        try:
            conn.execute(
                "INSERT INTO tasks (source, confidence, metadata_json, content, schema_version) VALUES (?, ?, ?, ?, ?)",
                (source, 1.0, metadata_json, status, store.SCHEMA_VERSION),
            )
            conn.commit()
        finally:
            conn.close()

    store.submit_write(_write, timeout=30.0)
    _emit("record_task")


def _recommend_supersession_from_contradictions(
    reference: str,
    *,
    contradiction_candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    recommendation = {
        "recommended": False,
        "target_reference": None,
        "reason": "no_candidates",
        "signal": 0.0,
        "auto_applied": False,
    }
    if not contradiction_candidates:
        return recommendation

    signal_threshold = float(os.environ.get("OCMEMOG_GOVERNANCE_SUPERSESSION_RECOMMEND_SIGNAL", "0.9") or 0.9)
    model_conf_threshold = float(os.environ.get("OCMEMOG_GOVERNANCE_SUPERSESSION_MODEL_CONFIDENCE", "0.9") or 0.9)
    ranked = sorted(contradiction_candidates, key=lambda item: float(item.get("signal") or 0.0), reverse=True)
    top = ranked[0]
    signal = float(top.get("signal") or 0.0)
    model_hint = top.get("model_hint") if isinstance(top.get("model_hint"), dict) else {}
    model_contradiction = bool(model_hint.get("contradiction"))
    model_confidence = float(model_hint.get("confidence") or 0.0)

    if signal < signal_threshold:
        recommendation["reason"] = "signal_below_threshold"
        recommendation["signal"] = signal
        return recommendation

    if model_hint and (not model_contradiction or model_confidence < model_conf_threshold):
        recommendation["reason"] = "model_hint_not_strong_enough"
        recommendation["signal"] = signal
        return recommendation

    target = str(top.get("reference") or "")
    if not target:
        recommendation["reason"] = "missing_target"
        recommendation["signal"] = signal
        return recommendation

    recommendation.update({
        "recommended": True,
        "target_reference": target,
        "reason": "high_confidence_contradiction",
        "signal": signal,
        "model_hint": model_hint,
    })

    return recommendation


def _canonicalize_duplicate_target(reference: str) -> str:
    payload = provenance.fetch_reference(reference) or {}
    metadata = payload.get("metadata") or {}
    prov = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
    canonical = str(prov.get("canonical_reference") or prov.get("duplicate_of") or reference).strip()
    return canonical or reference


def _token_signature(text: str) -> frozenset[str]:
    return frozenset(_tokenize(text))


def _auto_promote_duplicate_candidate(
    reference: str,
    *,
    duplicate_candidates: List[Dict[str, Any]],
    contradiction_candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    auto_promote_enabled = os.environ.get("OCMEMOG_GOVERNANCE_AUTOPROMOTE", "true").strip().lower() in {"1", "true", "yes"}
    allow_with_contradictions = os.environ.get("OCMEMOG_GOVERNANCE_AUTOPROMOTE_ALLOW_CONTRADICTIONS", "false").strip().lower() in {"1", "true", "yes"}
    duplicate_threshold = float(os.environ.get("OCMEMOG_GOVERNANCE_DUPLICATE_AUTOPROMOTE_SIMILARITY", "0.98") or 0.98)
    duplicate_margin = float(os.environ.get("OCMEMOG_GOVERNANCE_DUPLICATE_AUTOPROMOTE_MARGIN", "0.02") or 0.02)
    require_exact_tokens = os.environ.get("OCMEMOG_GOVERNANCE_DUPLICATE_AUTOPROMOTE_REQUIRE_EXACT_TOKENS", "true").strip().lower() in {"1", "true", "yes"}
    promoted: Dict[str, Any] = {"duplicate_of": None, "promoted": False, "reason": "disabled" if not auto_promote_enabled else "none"}

    if not auto_promote_enabled:
        return promoted

    if contradiction_candidates and not allow_with_contradictions:
        promoted["reason"] = "blocked_by_contradiction_candidates"
        return promoted

    if not duplicate_candidates:
        promoted["reason"] = "no_duplicate_candidates"
        return promoted

    payload = provenance.fetch_reference(reference) or {}
    reference_content = str(payload.get("content") or "")
    reference_signature = _token_signature(reference_content)
    ranked = sorted(duplicate_candidates, key=lambda item: float(item.get("similarity") or 0.0), reverse=True)
    top = ranked[0]
    similarity = float(top.get("similarity") or 0.0)
    target = _canonicalize_duplicate_target(str(top.get("reference") or ""))
    if not target or target == reference or similarity < duplicate_threshold:
        promoted["reason"] = "similarity_below_threshold"
        return promoted

    if len(ranked) > 1:
        runner_up = float(ranked[1].get("similarity") or 0.0)
        if similarity - runner_up < duplicate_margin:
            promoted["reason"] = "ambiguous_duplicate_candidates"
            return promoted

    target_payload = provenance.fetch_reference(target) or {}
    target_content = str(target_payload.get("content") or "")
    if require_exact_tokens and _token_signature(target_content) != reference_signature:
        promoted["reason"] = "token_signature_mismatch"
        return promoted

    merged = mark_memory_relationship(reference, relationship="duplicate_of", target_reference=target, status="duplicate")
    promoted.update({
        "duplicate_of": target,
        "promoted": merged is not None,
        "reason": "duplicate_high_confidence" if merged is not None else "promotion_failed",
        "similarity": similarity,
    })
    return promoted


def _auto_apply_supersession_recommendation(
    reference: str,
    *,
    contradiction_candidates: List[Dict[str, Any]],
    supersession_recommendation: Dict[str, Any],
) -> Dict[str, Any]:
    recommendation = dict(supersession_recommendation or {})
    if not recommendation:
        return {"recommended": False, "auto_applied": False, "reason": "missing_recommendation", "target_reference": None, "signal": 0.0}

    auto_apply = os.environ.get("OCMEMOG_GOVERNANCE_AUTOPROMOTE_SUPERSESSION", "false").strip().lower() in {"1", "true", "yes"}
    allow_with_contradictions = os.environ.get("OCMEMOG_GOVERNANCE_AUTOPROMOTE_ALLOW_CONTRADICTIONS", "false").strip().lower() in {"1", "true", "yes"}
    auto_apply_signal = float(os.environ.get("OCMEMOG_GOVERNANCE_SUPERSESSION_AUTOPROMOTE_SIGNAL", "0.97") or 0.97)
    model_conf_threshold = float(os.environ.get("OCMEMOG_GOVERNANCE_SUPERSESSION_AUTOPROMOTE_MODEL_CONFIDENCE", "0.97") or 0.97)

    recommendation.setdefault("auto_applied", False)
    if not recommendation.get("recommended"):
        recommendation["reason"] = recommendation.get("reason") or "not_recommended"
        return recommendation

    if not auto_apply:
        return recommendation

    if contradiction_candidates and not allow_with_contradictions:
        recommendation["reason"] = "blocked_by_contradiction_candidates"
        return recommendation

    signal = float(recommendation.get("signal") or 0.0)
    if signal < auto_apply_signal:
        recommendation["reason"] = "signal_below_autopromote_threshold"
        return recommendation

    model_hint = recommendation.get("model_hint") if isinstance(recommendation.get("model_hint"), dict) else {}
    if not model_hint or not model_hint.get("contradiction") or float(model_hint.get("confidence") or 0.0) < model_conf_threshold:
        recommendation["reason"] = "model_hint_below_autopromote_threshold"
        return recommendation

    target = str(recommendation.get("target_reference") or "").strip()
    if not target or target == reference:
        recommendation["reason"] = "missing_target"
        return recommendation

    merged = mark_memory_relationship(reference, relationship="supersedes", target_reference=target, status="active")
    recommendation["auto_applied"] = merged is not None
    recommendation["reason"] = "auto_applied_supersession" if merged is not None else "auto_apply_failed"
    return recommendation


def _auto_attach_governance_candidates(reference: str, *, use_model: bool = True) -> Dict[str, Any]:
    payload = provenance.fetch_reference(reference) or {}
    content = str(payload.get("content") or "")
    if not _governance_candidates_significant(content):
        return {
            "duplicate_candidates": [],
            "contradiction_candidates": [],
            "auto_promotion": {"duplicate_of": None, "promoted": False, "reason": "insufficient_governance_signal"},
            "supersession_recommendation": {
                "recommended": False,
                "auto_applied": False,
                "reason": "insufficient_governance_signal",
            },
        }

    duplicate_candidates = find_duplicate_candidates(reference, limit=5, min_similarity=0.72)
    contradiction_candidates = find_contradiction_candidates(
        reference,
        limit=5,
        min_signal=0.55,
        use_model=use_model,
        max_model_hints=_auto_attach_model_hint_budget() if use_model else 0,
    )
    supersession_recommendation = _recommend_supersession_from_contradictions(
        reference,
        contradiction_candidates=contradiction_candidates,
    )
    auto_promotion = _auto_promote_duplicate_candidate(
        reference,
        duplicate_candidates=duplicate_candidates,
        contradiction_candidates=contradiction_candidates,
    )
    if supersession_recommendation.get("recommended"):
        target_reference = str(supersession_recommendation.get("target_reference") or "").strip()
        target_payload = provenance.fetch_reference(target_reference) or {} if target_reference else {}
        supersession_recommendation["plain_english"] = _plain_english_supersession_summary(
            reference=reference,
            target_reference=target_reference,
            source_content=str(payload.get("content") or ""),
            target_content=str(target_payload.get("content") or ""),
            reason=str(supersession_recommendation.get("reason") or ""),
        )

    supersession_recommendation = _auto_apply_supersession_recommendation(
        reference,
        contradiction_candidates=contradiction_candidates,
        supersession_recommendation=supersession_recommendation,
    )
    payload = {
        "duplicate_candidates": [item.get("reference") for item in duplicate_candidates if item.get("reference")],
        "contradiction_candidates": [item.get("reference") for item in contradiction_candidates if item.get("reference")],
        "auto_promotion": auto_promotion,
        "supersession_recommendation": supersession_recommendation,
    }
    provenance.update_memory_metadata(reference, payload)
    emit_event(
        store.state_store.report_log_path(),
        "store_memory_governance_candidates",
        status="ok",
        reference=reference,
        duplicates=len(payload["duplicate_candidates"]),
        contradictions=len(payload["contradiction_candidates"]),
        auto_promoted=bool(auto_promotion.get("promoted")),
        auto_promotion_reason=str(auto_promotion.get("reason") or "none"),
        supersession_recommended=bool(supersession_recommendation.get("recommended")),
        supersession_auto_applied=bool(supersession_recommendation.get("auto_applied")),
        supersession_reason=str(supersession_recommendation.get("reason") or "none"),
    )
    return payload


def store_memory(
    memory_type: str,
    content: str,
    *,
    source: str | None = None,
    metadata: Dict[str, Any] | None = None,
    timestamp: str | None = None,
    post_process: bool = True,
    skip_embedding_provider: bool = False,
) -> int:
    content = _sanitize(content)
    table = memory_type.strip().lower() if memory_type else "knowledge"
    allowed = set(store.MEMORY_TABLES)
    if table not in allowed:
        table = "knowledge"
    normalized_metadata = provenance.normalize_metadata(metadata, source=source)

    def _write() -> int:
        conn = store.connect()
        try:
            if timestamp:
                cur = conn.execute(
                    f"INSERT INTO {table} (source, confidence, metadata_json, content, schema_version, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                    (source, 1.0, json.dumps(normalized_metadata, ensure_ascii=False), content, store.SCHEMA_VERSION, timestamp),
                )
            else:
                cur = conn.execute(
                    f"INSERT INTO {table} (source, confidence, metadata_json, content, schema_version) VALUES (?, ?, ?, ?, ?)",
                    (source, 1.0, json.dumps(normalized_metadata, ensure_ascii=False), content, store.SCHEMA_VERSION),
                )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    last_row_id = store.submit_write(_write, timeout=30.0)
    reference = f"{table}:{last_row_id}"
    provenance.apply_links(reference, normalized_metadata)
    if post_process:
        try:
            from . import vector_index

            vector_index.insert_memory(last_row_id, content, 1.0, source_type=table, skip_provider=skip_embedding_provider)
        except Exception as exc:
            emit_event(store.state_store.report_log_path(), "store_memory_index_failed", status="error", error=str(exc), memory_type=table)
        try:
            _auto_attach_governance_candidates(reference, use_model=_auto_attach_model_hints_enabled())
        except Exception as exc:
            emit_event(
                store.state_store.report_log_path(),
                "store_memory_governance_failed",
                status="error",
                error=str(exc),
                reference=reference,
            )
    _emit("store_memory")
    return last_row_id


def postprocess_stored_memory(
    reference: str,
    *,
    run_embedding: bool = True,
    run_governance: bool = True,
    skip_embedding_provider: bool = False,
) -> Dict[str, Any]:
    parsed = _parse_memory_reference(reference)
    if not parsed:
        emit_event(
            store.state_store.report_log_path(),
            "store_memory_postprocess_skipped",
            status="error",
            reason="invalid_reference",
            reference=reference,
        )
        return {"ok": False, "reference": reference, "error": "invalid_reference"}
    table, identifier = parsed
    if table not in set(store.MEMORY_TABLES):
        emit_event(
            store.state_store.report_log_path(),
            "store_memory_postprocess_skipped",
            status="error",
            reason="invalid_table",
            reference=reference,
            table=table,
        )
        return {"ok": False, "reference": reference, "error": "invalid_table"}

    row = provenance.fetch_reference(reference)
    if not row:
        emit_event(
            store.state_store.report_log_path(),
            "store_memory_postprocess_skipped",
            status="error",
            reason="missing_memory",
            reference=reference,
        )
        return {"ok": False, "reference": reference, "error": "missing_memory"}

    content = str(row.get("content") or "")
    memory_id = int(identifier)
    if run_embedding:
        try:
            from . import vector_index

            vector_index.insert_memory(
                memory_id,
                content,
                float(row.get("confidence") or 1.0),
                source_type=table,
                skip_provider=skip_embedding_provider,
            )
        except Exception as exc:
            emit_event(
                store.state_store.report_log_path(),
                "store_memory_index_failed",
                status="error",
                error=str(exc),
                reference=reference,
            )
            return {"ok": False, "reference": reference, "error": str(exc)}

    if run_governance:
        try:
            _auto_attach_governance_candidates(reference, use_model=_auto_attach_model_hints_enabled())
        except Exception as exc:
            emit_event(
                store.state_store.report_log_path(),
                "store_memory_postprocess_governance_failed",
                status="error",
                error=str(exc),
                reference=reference,
            )
            return {"ok": False, "reference": reference, "error": str(exc)}

    emit_event(
        store.state_store.report_log_path(),
        "store_memory_postprocess_complete",
        status="ok",
        reference=reference,
    )
    return {"ok": True, "reference": reference}


def record_reinforcement(task_id: str, outcome: str, note: str, *, source_module: str | None = None) -> None:
    outcome = _sanitize(outcome)
    note = _sanitize(note)
    memory_reference = f"reinforcement:{task_id or 'unknown'}:{source_module or 'unspecified'}"
    def _write() -> None:
        conn = store.connect()
        try:
            conn.execute(
                "INSERT INTO experiences (task_id, outcome, reward_score, confidence, memory_reference, experience_type, source_module, schema_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, outcome, None, 1.0, memory_reference, "reinforcement", source_module, store.SCHEMA_VERSION),
            )
            conn.execute(
                "INSERT INTO memory_events (event_type, source, details_json, schema_version) VALUES (?, ?, ?, ?)",
                ("reinforcement_note", source_module, json.dumps({"task_id": task_id, "note": note, "memory_reference": memory_reference}), store.SCHEMA_VERSION),
            )
            conn.commit()
        finally:
            conn.close()

    store.submit_write(_write, timeout=30.0)
    _emit("record_reinforcement")


def _tokenize(text: str) -> List[str]:
    return [token for token in "".join(ch.lower() if ch.isalnum() else " " for ch in (text or "")).split() if token]


def _similarity(left: str, right: str) -> float:
    left_tokens = set(_tokenize(left))
    right_tokens = set(_tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return round(overlap / max(1, union), 3)


def _extract_literals(text: str) -> List[str]:
    import re
    patterns = [
        r"\b\d{2,6}\b",
        r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
        r"\b\+?1?\d{10,11}\b",
        r"\b[a-zA-Z][a-zA-Z0-9_.-]*:[0-9]{2,5}\b",
    ]
    hits: List[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text or ""):
            value = str(match).strip()
            if value and value not in hits:
                hits.append(value)
    return hits


def _contradiction_signal(left: str, right: str) -> float:
    left_tokens = set(_tokenize(left))
    right_tokens = set(_tokenize(right))
    literals_left = set(_extract_literals(left))
    literals_right = set(_extract_literals(right))
    shared_context = len((left_tokens & right_tokens) - literals_left - literals_right)
    different_literals = literals_left.symmetric_difference(literals_right)
    lexical_similarity = _similarity(left, right)
    if not literals_left and not literals_right:
        return 0.0
    if literals_left == literals_right:
        return 0.0
    if shared_context < 2 and lexical_similarity < 0.45:
        return 0.0
    base = min(1.0, 0.35 * lexical_similarity + 0.12 * shared_context + 0.3 * min(2, len(different_literals)))
    return round(base, 3)


def _model_contradiction_hint(left: str, right: str) -> Optional[Dict[str, Any]]:
    prompt = (
        "You are checking whether two short memory statements likely contradict each other.\n"
        "Return strict JSON with keys: contradiction (true/false), confidence (0..1), rationale (string).\n"
        f"Statement A: {left}\n"
        f"Statement B: {right}\n"
    )
    result = inference.infer(
        prompt,
        provider_name=os.environ.get("OCMEMOG_PONDER_MODEL", "local-openai:qwen2.5-7b-instruct"),
    )
    if result.get("status") != "ok":
        return None
    try:
        parsed = json.loads(result.get("output") or "{}")
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    return {
        "contradiction": bool(parsed.get("contradiction")),
        "confidence": float(parsed.get("confidence") or 0.0),
        "rationale": str(parsed.get("rationale") or "").strip(),
    }


def find_duplicate_candidates(
    reference: str,
    *,
    limit: int = 5,
    min_similarity: float = 0.72,
) -> List[Dict[str, Any]]:
    payload = provenance.fetch_reference(reference) or {}
    table = str(payload.get("table") or payload.get("type") or "")
    content = str(payload.get("content") or "")
    if table not in set(store.MEMORY_TABLES):
        return []
    if not _governance_candidates_significant(content):
        return []
    row_id = payload.get("id")
    content_tokens = set(_tokenize(content))
    conn = store.connect()
    try:
        rows = conn.execute(
            f"SELECT id, content, metadata_json, timestamp FROM {table} WHERE id != ? ORDER BY id DESC LIMIT ?",
            (int(row_id), max(limit * 10, 50)),
        ).fetchall()
    finally:
        conn.close()

    candidates: List[Dict[str, Any]] = []
    for row in rows:
        candidate_ref = f"{table}:{row['id'] if isinstance(row, dict) else row[0]}"
        candidate_content = row["content"] if isinstance(row, dict) else row[1]
        candidate_tokens = set(_tokenize(candidate_content))
        if not candidate_tokens:
            continue
        overlap_tokens = content_tokens & candidate_tokens
        if not overlap_tokens:
            continue
        overlap = len(overlap_tokens)
        union = len(content_tokens | candidate_tokens)
        if union <= 0:
            continue
        score = round(overlap / union, 3)
        if score < min_similarity:
            continue
        meta_raw = row["metadata_json"] if isinstance(row, dict) else row[2]
        try:
            metadata = json.loads(meta_raw or "{}")
        except Exception:
            metadata = {}
        preview = provenance.preview_from_metadata(metadata)
        candidates.append({
            "reference": candidate_ref,
            "content": candidate_content,
            "similarity": score,
            "timestamp": row["timestamp"] if isinstance(row, dict) else row[3],
            "provenance_preview": preview,
        })

    candidates.sort(key=lambda item: item["similarity"], reverse=True)
    top = candidates[:limit]
    if top:
        provenance.force_update_memory_metadata(reference, {"duplicate_candidates": [item["reference"] for item in top]})
        _emit("find_duplicate_candidates")
    return top


def find_contradiction_candidates(
    reference: str,
    *,
    limit: int = 5,
    min_signal: float = 0.55,
    use_model: bool = True,
    max_model_hints: int | None = None,
) -> List[Dict[str, Any]]:
    payload = provenance.fetch_reference(reference) or {}
    table = str(payload.get("table") or payload.get("type") or "")
    content = str(payload.get("content") or "")
    if table not in set(store.MEMORY_TABLES):
        return []
    if not _governance_candidates_significant(content):
        return []
    row_id = payload.get("id")
    content_tokens = set(_tokenize(content))
    content_literals = set(_extract_literals(content))
    conn = store.connect()
    try:
        rows = conn.execute(
            f"SELECT id, content, metadata_json, timestamp FROM {table} WHERE id != ? ORDER BY id DESC LIMIT ?",
            (int(row_id), max(limit * 12, 60)),
        ).fetchall()
    finally:
        conn.close()

    candidates: List[Dict[str, Any]] = []
    for row in rows:
        candidate_ref = f"{table}:{row['id'] if isinstance(row, dict) else row[0]}"
        candidate_content = row["content"] if isinstance(row, dict) else row[1]
        candidate_tokens = set(_tokenize(candidate_content))
        if not candidate_tokens:
            continue
        candidate_literal_values = _extract_literals(candidate_content)
        candidate_literals = set(candidate_literal_values)
        overlap_tokens = content_tokens & candidate_tokens
        if not overlap_tokens:
            continue
        shared_context = len(overlap_tokens - content_literals - candidate_literals)
        union = len(content_tokens | candidate_tokens)
        lexical_similarity = round(len(overlap_tokens) / max(1, union), 3)
        if not content_literals and not candidate_literals:
            signal = 0.0
        elif content_literals == candidate_literals:
            signal = 0.0
        elif shared_context < 2 and lexical_similarity < 0.45:
            signal = 0.0
        else:
            signal = round(
                min(1.0, 0.35 * lexical_similarity + 0.12 * shared_context + 0.3 * min(2, len(content_literals.symmetric_difference(candidate_literals)))),
                3,
            )
        if signal < min_signal:
            continue
        meta_raw = row["metadata_json"] if isinstance(row, dict) else row[2]
        try:
            metadata = json.loads(meta_raw or "{}")
        except Exception:
            metadata = {}
        preview = provenance.preview_from_metadata(metadata)
        item: Dict[str, Any] = {
            "reference": candidate_ref,
            "content": candidate_content,
            "signal": signal,
            "timestamp": row["timestamp"] if isinstance(row, dict) else row[3],
            "provenance_preview": preview,
            "literals": candidate_literal_values,
        }
        candidates.append(item)

    candidates.sort(key=lambda item: item["signal"], reverse=True)

    if use_model:
        hint_budget = len(candidates) if max_model_hints is None else max(0, int(max_model_hints))
        for item in candidates[:hint_budget]:
            candidate_content = item.get("content")
            hint = _model_contradiction_hint(content, str(candidate_content))
            if not hint:
                continue
            item["model_hint"] = hint
            if not hint.get("contradiction") and float(item.get("signal") or 0.0) < 0.8:
                continue
            item["signal"] = round(max(float(item.get("signal") or 0.0), float(hint.get("confidence") or 0.0)), 3)

        candidates = [
            item
            for item in candidates
            if not (item.get("model_hint") is not None and not item["model_hint"].get("contradiction") and float(item.get("signal") or 0.0) < 0.8)
        ]
        candidates.sort(key=lambda item: item["signal"], reverse=True)

    top = candidates[:limit]
    if top:
        provenance.force_update_memory_metadata(reference, {"contradicts": [item["reference"] for item in top], "contradiction_status": "candidate", "contradiction_candidates": [item["reference"] for item in top]})
        _emit("find_contradiction_candidates")
    return top


def mark_memory_relationship(
    reference: str,
    *,
    relationship: str,
    target_reference: str,
    status: str | None = None,
) -> Dict[str, Any] | None:
    relationship = (relationship or "").strip().lower()
    updates: Dict[str, Any] = {}
    if relationship == "supersedes":
        updates = {
            "supersedes": target_reference,
            "memory_status": status or "active",
            "canonical_reference": reference,
        }
        provenance.force_update_memory_metadata(target_reference, {
            "superseded_by": reference,
            "memory_status": "superseded",
            "canonical_reference": reference,
        })
    elif relationship == "duplicate_of":
        updates = {
            "duplicate_of": target_reference,
            "memory_status": status or "duplicate",
            "canonical_reference": target_reference,
        }
    elif relationship == "contradicts":
        updates = {
            "contradicts": [target_reference],
            "contradiction_status": status or "contested",
            "memory_status": "contested",
        }
        provenance.force_update_memory_metadata(target_reference, {
            "contradicts": [reference],
            "contradiction_status": status or "contested",
            "memory_status": "contested",
        })
    else:
        return None
    merged = provenance.force_update_memory_metadata(reference, updates)
    _emit(f"mark_memory_relationship_{relationship}")
    return merged


def list_governance_candidates(
    *,
    categories: Optional[List[str]] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    allowed = set(store.MEMORY_TABLES)
    tables = [table for table in (categories or list(allowed)) if table in allowed]
    conn = store.connect()
    try:
        items: List[Dict[str, Any]] = []
        for table in tables:
            rows = conn.execute(
                f"SELECT id, timestamp, content, metadata_json FROM {table} ORDER BY id DESC LIMIT ?",
                (max(limit, 20),),
            ).fetchall()
            for row in rows:
                metadata = json.loads((row["metadata_json"] if isinstance(row, dict) else row[3]) or "{}")
                prov = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
                duplicate_candidates = prov.get("duplicate_candidates") or []
                contradiction_candidates = prov.get("contradiction_candidates") or []
                supersession_recommendation = prov.get("supersession_recommendation") or {}
                if not duplicate_candidates and not contradiction_candidates and not supersession_recommendation:
                    continue
                items.append({
                    "reference": f"{table}:{row['id'] if isinstance(row, dict) else row[0]}",
                    "bucket": table,
                    "timestamp": row["timestamp"] if isinstance(row, dict) else row[1],
                    "content": row["content"] if isinstance(row, dict) else row[2],
                    "memory_status": prov.get("memory_status") or metadata.get("memory_status") or "active",
                    "duplicate_candidates": duplicate_candidates,
                    "contradiction_candidates": contradiction_candidates,
                    "supersession_recommendation": supersession_recommendation,
                })
        items.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        return items[:limit]
    finally:
        conn.close()


def _remove_from_list(values: Any, target: str) -> List[str]:
    return [str(item) for item in (values or []) if str(item) and str(item) != target]


def _review_item_context(reference: str, *, depth: int = 1) -> Dict[str, Any]:
    if depth <= 0:
        payload = provenance.fetch_reference(reference) or {"reference": reference}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        prov = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
        return {
            "reference": reference,
            "bucket": payload.get("table"),
            "id": payload.get("id"),
            "timestamp": payload.get("timestamp"),
            "content": payload.get("content"),
            "memory_status": prov.get("memory_status") or metadata.get("memory_status") or "active",
            "provenance_preview": provenance.preview_from_metadata(metadata),
            "metadata": metadata,
            "links": [],
            "backlinks": [],
        }

    payload = provenance.hydrate_reference(reference, depth=depth) or {"reference": reference}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    prov = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
    return {
        "reference": reference,
        "bucket": payload.get("table"),
        "id": payload.get("id"),
        "timestamp": payload.get("timestamp"),
        "content": payload.get("content"),
        "memory_status": prov.get("memory_status") or metadata.get("memory_status") or "active",
        "provenance_preview": payload.get("provenance_preview") or provenance.preview_from_metadata(metadata),
        "metadata": metadata,
        "links": payload.get("links") or [],
        "backlinks": payload.get("backlinks") or [],
    }


def _review_item_summary(kind: str, reference: str, target_reference: str) -> str:
    if kind == "duplicate_candidate":
        return f"{reference} may duplicate {target_reference}"
    if kind == "contradiction_candidate":
        return f"{reference} may contradict {target_reference}"
    if kind == "supersession_recommendation":
        return f"{reference} may supersede {target_reference}"
    return f"{reference} requires review against {target_reference}"


def _normalize_supersession_preview(text: str, fallback: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return fallback
    cleaned = _sanitize(raw)
    cleaned = re.sub(r"\[\[reply_to_current\]\]", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\([^\)]*assistant\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\b", "", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    lower = cleaned.lower()
    noisy_markers = [
        "provider monitor",
        "memory-sync",
        "launchctl",
        "reply_to_current",
        "openclaw gateway restart",
        "python3 -u -m",
        "logged to",
        "checkpoint saved",
    ]
    if any(marker in lower for marker in noisy_markers):
        return fallback
    if len(cleaned) > 140:
        sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0].strip()
        cleaned = sentence or cleaned[:140]
    cleaned = cleaned[:140].rstrip(" ,;:-")
    return cleaned or fallback


def _heuristic_supersession_summary(
    reference: str,
    target_reference: str,
    source_content: str,
    target_content: str,
    reason: str,
) -> str:
    source = _normalize_supersession_preview(source_content, "a newer consolidated memory")
    target = _normalize_supersession_preview(target_content, "an older noisier memory")
    because = _normalize_supersession_preview(reason, "the newer memory appears cleaner and more useful")
    return f"This newer memory probably replaces an older one: new = {source}; old = {target}; reason = {because}."[:220].rstrip()


def _plain_english_supersession_summary(
    *,
    reference: str,
    target_reference: str,
    source_content: str,
    target_content: str,
    reason: str,
) -> str:
    cache_key = json.dumps(
        {
            "reference": reference,
            "target_reference": target_reference,
            "source_content": source_content,
            "target_content": target_content,
            "reason": reason,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    cached = _SUPERSESSION_SUMMARY_CACHE.get(cache_key)
    if cached:
        return cached

    fallback = _heuristic_supersession_summary(reference, target_reference, source_content, target_content, reason)
    source_preview = _normalize_supersession_preview(source_content, "a newer consolidated memory")
    target_preview = _normalize_supersession_preview(target_content, "an older noisier memory")
    reason_preview = _normalize_supersession_preview(reason, "the newer memory appears cleaner and more useful")
    prompt = (
        "Rewrite this supersession recommendation as exactly one short plain-English sentence for a human dashboard. "
        "Describe the relationship only. Do not quote or repeat full memory contents. Do not use JSON, bullets, markdown, timestamps, or command text. Keep it under 160 characters.\n\n"
        f"Newer candidate reference: {reference}\n"
        f"Potentially replaced reference: {target_reference}\n"
        f"Newer candidate preview: {source_preview}\n"
        f"Older candidate preview: {target_preview}\n"
        f"Recommendation reason: {reason_preview}\n\n"
        "Plain-English dashboard sentence:"
    )
    result = _run_with_timeout(
        lambda: inference.infer(
            prompt,
            provider_name=os.environ.get("OCMEMOG_PONDER_MODEL", "local-openai:qwen2.5-7b-instruct"),
        ),
        1.5,
        {"status": "timeout", "output": ""},
    )
    output = str((result or {}).get("output") or "").strip()
    cleaned = output.replace("\n", " ").strip(" -:\t")
    cleaned = re.sub(r"^(summary|sentence|plain-english dashboard sentence)\s*:\s*", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = _normalize_supersession_preview(cleaned, "")
    if cleaned and len(cleaned) >= 24:
        summary = cleaned[:160]
    else:
        summary = fallback
    _SUPERSESSION_SUMMARY_CACHE[cache_key] = summary
    return summary


def _review_explanation(kind: str, *, signal: float, reason: str, source: Dict[str, Any], target: Dict[str, Any]) -> Dict[str, Any]:
    source_status = str(source.get("memory_status") or "active")
    target_status = str(target.get("memory_status") or "active")
    reason_text = str(reason or "").strip() or "no explicit rationale captured"
    if kind == "duplicate_candidate":
        short = f"Possible duplicate merge with signal {signal:.2f}."
    elif kind == "contradiction_candidate":
        short = f"Possible contradiction with signal {signal:.2f}."
    elif kind == "supersession_recommendation":
        short = f"Possible supersession with signal {signal:.2f}."
    else:
        short = f"Governance review item with signal {signal:.2f}."
    return {
        "short": short,
        "reason": reason_text,
        "source_status": source_status,
        "target_status": target_status,
    }


def _review_actions(kind: str, relationship: str) -> List[Dict[str, Any]]:
    meta = _REVIEW_KIND_METADATA.get(kind, {})
    return [
        {
            "decision": "approve",
            "approved": True,
            "relationship": relationship,
            "label": meta.get("approve_label") or "Approve",
        },
        {
            "decision": "reject",
            "approved": False,
            "relationship": relationship,
            "label": meta.get("reject_label") or "Reject",
        },
    ]


def _relationship_for_review(kind: str | None = None, relationship: str | None = None) -> str:
    resolved = (relationship or "").strip().lower()
    if resolved:
        return resolved
    kind_key = (kind or "").strip().lower()
    return _REVIEW_KIND_METADATA.get(kind_key, {}).get("relationship", "")


def list_governance_review_items(
    *,
    categories: Optional[List[str]] = None,
    limit: int = 100,
    context_depth: int = 1,
    scan_limit: int = 3000,
) -> List[Dict[str, Any]]:
    items = governance_queue(categories=categories, limit=limit, scan_limit=scan_limit)
    review_items: List[Dict[str, Any]] = []
    for item in items:
        kind = str(item.get("kind") or "")
        relationship = _relationship_for_review(kind=kind)
        reference = str(item.get("reference") or "")
        target_reference = str(item.get("target_reference") or "")
        if not reference or not target_reference or not relationship:
            continue
        source = _review_item_context(reference, depth=context_depth)
        target = _review_item_context(target_reference, depth=context_depth)
        summary = _review_item_summary(kind, reference, target_reference)
        if kind == "supersession_recommendation":
            plain_english = str(item.get("plain_english") or "").strip()
            if plain_english:
                summary = plain_english
        signal = float(item.get("signal") or 0.0)
        reason = str(item.get("reason") or "")
        review_items.append({
            "review_id": f"{kind}:{reference}->{target_reference}",
            "kind": kind,
            "kind_label": _REVIEW_KIND_METADATA.get(kind, {}).get("label") or kind.replace("_", " "),
            "relationship": relationship,
            "priority": int(item.get("priority") or 0),
            "timestamp": item.get("timestamp"),
            "bucket": item.get("bucket"),
            "signal": signal,
            "reason": reason,
            "reference": reference,
            "target_reference": target_reference,
            "summary": summary,
            "explanation": _review_explanation(kind, signal=signal, reason=reason, source=source, target=target),
            "actions": _review_actions(kind, relationship),
            "source": source,
            "target": target,
        })
    return review_items


def apply_governance_decision(
    reference: str,
    *,
    relationship: str,
    target_reference: str,
    approved: bool = True,
) -> Dict[str, Any] | None:
    relationship = (relationship or "").strip().lower()
    if approved:
        merged = mark_memory_relationship(reference, relationship=relationship, target_reference=target_reference)
        if merged is None:
            return None
        updates: Dict[str, Any] = {}
        if relationship == "duplicate_of":
            current = provenance.fetch_reference(reference) or {}
            metadata = current.get("metadata") or {}
            prov = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
            updates["duplicate_candidates"] = _remove_from_list(prov.get("duplicate_candidates"), target_reference)
        elif relationship == "contradicts":
            current = provenance.fetch_reference(reference) or {}
            metadata = current.get("metadata") or {}
            prov = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
            updates["contradiction_candidates"] = _remove_from_list(prov.get("contradiction_candidates"), target_reference)
        elif relationship == "supersedes":
            updates["supersession_recommendation"] = None
        if updates:
            merged = provenance.force_update_memory_metadata(reference, updates) or merged
        _emit(f"apply_governance_decision_{relationship}_approved")
        return merged

    current = provenance.fetch_reference(reference) or {}
    metadata = current.get("metadata") or {}
    prov = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
    updates: Dict[str, Any] = {}
    if relationship == "duplicate_of":
        updates["duplicate_candidates"] = _remove_from_list(prov.get("duplicate_candidates"), target_reference)
    elif relationship == "contradicts":
        updates["contradiction_candidates"] = _remove_from_list(prov.get("contradiction_candidates"), target_reference)
    elif relationship == "supersedes":
        recommendation = prov.get("supersession_recommendation") if isinstance(prov.get("supersession_recommendation"), dict) else {}
        if not recommendation or str(recommendation.get("target_reference") or "") == target_reference:
            updates["supersession_recommendation"] = None
        updates["supersedes"] = None
    else:
        return None
    merged = provenance.force_update_memory_metadata(reference, updates)
    _emit(f"apply_governance_decision_{relationship}_{'approved' if approved else 'rejected'}")
    return merged


def apply_governance_review_decision(
    reference: str,
    *,
    target_reference: str,
    approved: bool = True,
    kind: str | None = None,
    relationship: str | None = None,
    context_depth: int = 1,
) -> Dict[str, Any] | None:
    resolved_relationship = _relationship_for_review(kind=kind, relationship=relationship)
    if not resolved_relationship:
        return None
    result = apply_governance_decision(
        reference,
        relationship=resolved_relationship,
        target_reference=target_reference,
        approved=approved,
    )
    if result is None:
        return None
    resolved_kind = (kind or "").strip().lower()
    if not resolved_kind:
        for candidate_kind, meta in _REVIEW_KIND_METADATA.items():
            if meta.get("relationship") == resolved_relationship:
                resolved_kind = candidate_kind
                break
    return {
        "reference": reference,
        "target_reference": target_reference,
        "approved": bool(approved),
        "kind": resolved_kind or None,
        "relationship": resolved_relationship,
        "result": result,
        "source": _review_item_context(reference, depth=context_depth),
        "target": _review_item_context(target_reference, depth=context_depth),
    }


def rollback_governance_decision(
    reference: str,
    *,
    relationship: str,
    target_reference: str,
) -> Dict[str, Any] | None:
    relationship = (relationship or "").strip().lower()
    if relationship not in {"duplicate_of", "supersedes", "contradicts"}:
        return None

    reference_payload = provenance.fetch_reference(reference) or {}
    ref_meta = reference_payload.get("metadata") or {}
    ref_prov = ref_meta.get("provenance") if isinstance(ref_meta.get("provenance"), dict) else {}

    if relationship == "duplicate_of":
        updates = {
            "duplicate_of": None,
            "memory_status": "active",
            "canonical_reference": None,
        }
        merged = provenance.force_update_memory_metadata(reference, updates)
        _emit("rollback_governance_duplicate_of")
        return merged

    if relationship == "supersedes":
        provenance.force_update_memory_metadata(reference, {"supersedes": None})
        target_updates = {
            "superseded_by": None,
            "memory_status": "active",
        }
        merged = provenance.force_update_memory_metadata(target_reference, target_updates)
        _emit("rollback_governance_supersedes")
        return merged

    if relationship == "contradicts":
        new_list = _remove_from_list(ref_prov.get("contradicts"), target_reference)
        merged = provenance.force_update_memory_metadata(reference, {
            "contradicts": new_list,
            "contradiction_status": None,
            "memory_status": "active",
        })
        target_payload = provenance.fetch_reference(target_reference) or {}
        target_meta = target_payload.get("metadata") or {}
        target_prov = target_meta.get("provenance") if isinstance(target_meta.get("provenance"), dict) else {}
        target_updates = {
            "contradicts": _remove_from_list(target_prov.get("contradicts"), reference),
            "contradiction_status": None,
            "memory_status": "active",
        }
        provenance.force_update_memory_metadata(target_reference, target_updates)
        _emit("rollback_governance_contradicts")
        return merged

    return None


def governance_queue(*, categories: Optional[List[str]] = None, limit: int = 100, scan_limit: int = 3000) -> List[Dict[str, Any]]:
    allowed = set(store.MEMORY_TABLES)
    tables = [table for table in (categories or list(allowed)) if table in allowed]
    per_table_scan_limit = max(int(scan_limit or 0), max(int(limit or 0), 1))
    conn = store.connect()
    try:
        items: List[Dict[str, Any]] = []
        for table in tables:
            rows = conn.execute(
                f"SELECT id, timestamp, content, metadata_json FROM {table} ORDER BY id DESC LIMIT ?",
                (per_table_scan_limit,),
            ).fetchall()
            for row in rows:
                reference = f"{table}:{row['id'] if isinstance(row, dict) else row[0]}"
                timestamp = row["timestamp"] if isinstance(row, dict) else row[1]
                content = row["content"] if isinstance(row, dict) else row[2]
                try:
                    metadata = json.loads((row["metadata_json"] if isinstance(row, dict) else row[3]) or "{}")
                except Exception:
                    metadata = {}
                prov = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
                duplicate_candidates = [str(x) for x in (prov.get("duplicate_candidates") or []) if x]
                contradiction_candidates = [str(x) for x in (prov.get("contradiction_candidates") or []) if x]
                supersession_recommendation = prov.get("supersession_recommendation") or {}

                for target in duplicate_candidates:
                    items.append({
                        "reference": reference,
                        "target_reference": target,
                        "kind": "duplicate_candidate",
                        "priority": 40,
                        "timestamp": timestamp,
                        "bucket": table,
                        "content": content,
                    })
                for target in contradiction_candidates:
                    items.append({
                        "reference": reference,
                        "target_reference": target,
                        "kind": "contradiction_candidate",
                        "priority": 70,
                        "timestamp": timestamp,
                        "bucket": table,
                        "content": content,
                    })
                if isinstance(supersession_recommendation, dict) and supersession_recommendation.get("recommended"):
                    items.append({
                        "reference": reference,
                        "target_reference": supersession_recommendation.get("target_reference"),
                        "kind": "supersession_recommendation",
                        "priority": 90,
                        "timestamp": timestamp,
                        "bucket": table,
                        "signal": float(supersession_recommendation.get("signal") or 0.0),
                        "reason": supersession_recommendation.get("reason"),
                        "content": content,
                    })
        items.sort(key=lambda item: (int(item.get("priority") or 0), str(item.get("timestamp") or "")), reverse=True)
        return items[:limit]
    finally:
        conn.close()


def _resolve_auto_resolve_policy(profile: str | None = None) -> Dict[str, Any]:
    preset = (profile or os.environ.get("OCMEMOG_GOVERNANCE_AUTORESOLVE_PROFILE", "conservative") or "conservative").strip().lower()
    presets = {
        "conservative": {
            "max_apply": 5,
            "allowed_kinds": {"duplicate_candidate", "supersession_recommendation"},
            "min_supersession_signal": 0.95,
            "allowed_buckets": set(),
        },
        "balanced": {
            "max_apply": 10,
            "allowed_kinds": {"duplicate_candidate", "supersession_recommendation"},
            "min_supersession_signal": 0.9,
            "allowed_buckets": set(),
        },
        "aggressive": {
            "max_apply": 20,
            "allowed_kinds": {"duplicate_candidate", "supersession_recommendation"},
            "min_supersession_signal": 0.85,
            "allowed_buckets": set(),
        },
    }
    policy = presets.get(preset, presets["conservative"]).copy()

    max_apply = os.environ.get("OCMEMOG_GOVERNANCE_AUTORESOLVE_MAX_APPLY")
    if max_apply:
        policy["max_apply"] = int(float(max_apply) or policy["max_apply"])
    allowed_kinds_raw = os.environ.get("OCMEMOG_GOVERNANCE_AUTORESOLVE_ALLOW_KINDS")
    if allowed_kinds_raw:
        policy["allowed_kinds"] = {k.strip() for k in allowed_kinds_raw.split(",") if k.strip()}
    min_supersession_signal = os.environ.get("OCMEMOG_GOVERNANCE_AUTORESOLVE_MIN_SUPERSESSION_SIGNAL")
    if min_supersession_signal:
        policy["min_supersession_signal"] = float(min_supersession_signal or policy["min_supersession_signal"])
    allowed_buckets_raw = os.environ.get("OCMEMOG_GOVERNANCE_AUTORESOLVE_ALLOW_BUCKETS")
    if allowed_buckets_raw is not None and allowed_buckets_raw != "":
        policy["allowed_buckets"] = {k.strip() for k in allowed_buckets_raw.split(",") if k.strip()}

    policy["profile"] = preset
    return policy


def governance_auto_resolve(
    *,
    categories: Optional[List[str]] = None,
    limit: int = 20,
    dry_run: bool = True,
    profile: str | None = None,
) -> Dict[str, Any]:
    queue = governance_queue(categories=categories, limit=limit)
    actions: List[Dict[str, Any]] = []
    applied = 0
    skipped = 0

    policy = _resolve_auto_resolve_policy(profile)
    max_apply = int(policy["max_apply"])
    allowed_kinds = set(policy["allowed_kinds"])
    min_supersession_signal = float(policy["min_supersession_signal"])
    allowed_buckets = set(policy["allowed_buckets"]) if policy["allowed_buckets"] else set()

    for item in queue:
        kind = str(item.get("kind") or "")
        bucket = str(item.get("bucket") or "")
        reference = str(item.get("reference") or "")
        target = str(item.get("target_reference") or "")
        if not reference or not target:
            skipped += 1
            actions.append({"reference": reference, "target_reference": target, "kind": kind, "applied": False, "dry_run": bool(dry_run), "reason": "missing_reference"})
            continue

        if kind not in allowed_kinds:
            skipped += 1
            actions.append({"reference": reference, "target_reference": target, "kind": kind, "applied": False, "dry_run": bool(dry_run), "reason": "kind_not_allowed"})
            continue

        if allowed_buckets and bucket not in allowed_buckets:
            skipped += 1
            actions.append({"reference": reference, "target_reference": target, "kind": kind, "applied": False, "dry_run": bool(dry_run), "reason": "bucket_not_allowed"})
            continue

        relationship = None
        if kind == "supersession_recommendation":
            signal = float(item.get("signal") or 0.0)
            if signal < min_supersession_signal:
                skipped += 1
                actions.append({"reference": reference, "target_reference": target, "kind": kind, "applied": False, "dry_run": bool(dry_run), "reason": "signal_below_min"})
                continue
            relationship = "supersedes"
        elif kind == "duplicate_candidate":
            relationship = "duplicate_of"
        else:
            skipped += 1
            actions.append({"reference": reference, "target_reference": target, "kind": kind, "applied": False, "dry_run": bool(dry_run), "reason": "unsupported_kind"})
            continue

        if not dry_run and applied >= max_apply:
            skipped += 1
            actions.append({"reference": reference, "target_reference": target, "kind": kind, "relationship": relationship, "applied": False, "dry_run": False, "reason": "max_apply_reached"})
            continue

        if dry_run:
            actions.append({
                "reference": reference,
                "target_reference": target,
                "kind": kind,
                "relationship": relationship,
                "applied": False,
                "dry_run": True,
                "reason": "dry_run",
            })
            continue

        result = apply_governance_decision(
            reference,
            relationship=relationship,
            target_reference=target,
            approved=True,
        )
        ok = result is not None
        if ok:
            applied += 1
        else:
            skipped += 1
        actions.append({
            "reference": reference,
            "target_reference": target,
            "kind": kind,
            "relationship": relationship,
            "applied": ok,
            "dry_run": False,
            "reason": "applied" if ok else "apply_failed",
        })

    emit_event(
        store.state_store.report_log_path(),
        "governance_auto_resolve",
        status="ok",
        dry_run=bool(dry_run),
        considered=len(queue),
        applied=applied,
        skipped=skipped,
        max_apply=max_apply,
        allowed_kinds=",".join(sorted(allowed_kinds)),
        min_supersession_signal=min_supersession_signal,
        allowed_buckets=",".join(sorted(allowed_buckets)) if allowed_buckets else "*",
        profile=str(policy.get("profile") or "conservative"),
    )
    return {
        "considered": len(queue),
        "applied": applied,
        "skipped": skipped,
        "dry_run": bool(dry_run),
        "policy": {
            "profile": policy.get("profile") or "conservative",
            "max_apply": max_apply,
            "allowed_kinds": sorted(allowed_kinds),
            "min_supersession_signal": min_supersession_signal,
            "allowed_buckets": sorted(allowed_buckets) if allowed_buckets else ["*"],
        },
        "actions": actions,
    }


def governance_audit(*, limit: int = 100, kinds: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    logfile = store.state_store.report_log_path()
    if not logfile.exists():
        return []
    wanted = {k.strip() for k in (kinds or []) if k.strip()}
    if not wanted:
        wanted = {
            "store_memory_governance_candidates",
            "governance_auto_resolve",
            "mark_memory_relationship_supersedes",
            "mark_memory_relationship_duplicate_of",
            "mark_memory_relationship_contradicts",
            "apply_governance_decision_duplicate_of_approved",
            "apply_governance_decision_contradicts_approved",
            "apply_governance_decision_supersedes_approved",
            "apply_governance_decision_duplicate_of_rejected",
            "apply_governance_decision_contradicts_rejected",
            "apply_governance_decision_supersedes_rejected",
        }
    entries: List[Dict[str, Any]] = []
    try:
        with logfile.open("r", encoding="utf-8", errors="ignore") as handle:
            lines = handle.readlines()[-max(limit * 5, 200):]
    except Exception:
        return []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        event = str(payload.get("event") or payload.get("name") or "").strip()
        if event not in wanted:
            continue
        payload["event"] = event
        entries.append(payload)
        if len(entries) >= limit:
            break
    return list(reversed(entries))


def governance_summary(*, categories: Optional[List[str]] = None) -> Dict[str, Any]:
    allowed = set(store.MEMORY_TABLES)
    tables = [table for table in (categories or list(allowed)) if table in allowed]
    conn = store.connect()
    try:
        summary: Dict[str, Any] = {
            "tables": {},
            "totals": {
                "rows": 0,
                "pending_duplicates": 0,
                "pending_contradictions": 0,
                "recommended_supersessions": 0,
                "status_active": 0,
                "status_duplicate": 0,
                "status_superseded": 0,
                "status_contested": 0,
            },
        }
        for table in tables:
            rows = conn.execute(
                f"SELECT id, metadata_json FROM {table} ORDER BY id DESC LIMIT 5000"
            ).fetchall()
            table_stats = {
                "rows": 0,
                "pending_duplicates": 0,
                "pending_contradictions": 0,
                "recommended_supersessions": 0,
                "status_active": 0,
                "status_duplicate": 0,
                "status_superseded": 0,
                "status_contested": 0,
            }
            for row in rows:
                table_stats["rows"] += 1
                try:
                    metadata = json.loads((row["metadata_json"] if isinstance(row, dict) else row[1]) or "{}")
                except Exception:
                    metadata = {}
                prov = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
                status = str(prov.get("memory_status") or metadata.get("memory_status") or "active").strip().lower()
                if status not in {"active", "duplicate", "superseded", "contested"}:
                    status = "active"
                table_stats[f"status_{status}"] += 1

                dup = prov.get("duplicate_candidates") or []
                contra = prov.get("contradiction_candidates") or []
                if dup:
                    table_stats["pending_duplicates"] += 1
                if contra:
                    table_stats["pending_contradictions"] += 1
                rec = prov.get("supersession_recommendation") or {}
                if isinstance(rec, dict) and rec.get("recommended"):
                    table_stats["recommended_supersessions"] += 1

            summary["tables"][table] = table_stats
            for key in summary["totals"].keys():
                summary["totals"][key] += int(table_stats.get(key, 0) or 0)
        return summary
    finally:
        conn.close()


def get_recent_events(limit: int = 10) -> List[Dict[str, Any]]:
    conn = store.connect()
    rows = conn.execute(
        "SELECT id, timestamp, event_type, source, details_json FROM memory_events ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_recent_tasks(limit: int = 10) -> List[Dict[str, Any]]:
    conn = store.connect()
    rows = conn.execute(
        "SELECT id, timestamp, source, confidence, metadata_json, content FROM tasks ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_memories(limit: int = 10) -> List[Dict[str, Any]]:
    conn = store.connect()
    rows = conn.execute(
        "SELECT id, timestamp, source, confidence, metadata_json, content FROM knowledge ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
