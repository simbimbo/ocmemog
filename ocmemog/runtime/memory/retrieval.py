from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Any, Iterable, Tuple, Optional

import json
import os

from ocmemog.runtime import state_store
from ocmemog.runtime.instrumentation import emit_event
from . import memory_links, provenance, store, vector_index


_LAST_RETRIEVAL_DIAGNOSTICS: Dict[str, Any] = {}


def _tokenize(text: str) -> List[str]:
    return [token for token in "".join(ch.lower() if ch.isalnum() else " " for ch in (text or "")).split() if token]


def _match_score(text: str, query: str) -> float:
    if not text or not query:
        return 0.0
    text_l = text.lower()
    query_l = query.lower().strip()
    if not query_l:
        return 0.0
    if query_l in text_l:
        return 1.0

    query_tokens = _tokenize(query_l)
    if not query_tokens:
        return 0.0
    text_tokens = _tokenize(text_l)
    if not text_tokens:
        return 0.0

    query_set = set(query_tokens)
    text_set = set(text_tokens)
    overlap_ratio = len(query_set & text_set) / max(1, len(query_set))

    ordered_hits = 0
    longest_run = 0
    current_run = 0
    text_len = len(text_tokens)
    query_len = len(query_tokens)
    for start in range(text_len):
        if text_tokens[start] != query_tokens[0]:
            continue
        run = 0
        while start + run < text_len and run < query_len and text_tokens[start + run] == query_tokens[run]:
            run += 1
        if run > 0:
            ordered_hits += 1
            longest_run = max(longest_run, run)
            current_run = max(current_run, run)

    sequence_ratio = longest_run / max(1, query_len)

    prefix_hits = 0
    for query_token in query_set:
        if len(query_token) < 4:
            continue
        if any(text_token.startswith(query_token) or query_token.startswith(text_token) for text_token in text_set):
            prefix_hits += 1
    prefix_ratio = prefix_hits / max(1, len([token for token in query_set if len(token) >= 4])) if any(len(token) >= 4 for token in query_set) else 0.0

    score = (overlap_ratio * 0.55) + (sequence_ratio * 0.30) + (prefix_ratio * 0.15)
    if ordered_hits > 1 and longest_run >= 2:
        score += 0.05
    return round(min(0.98, score), 3)


def _recency_score(timestamp: str | None) -> float:
    if not timestamp:
        return 0.0
    parsed = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            parsed = datetime.strptime(timestamp, fmt).replace(tzinfo=timezone.utc)
            break
        except ValueError:
            continue
    if parsed is None:
        return 0.0
    age_days = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0)
    if age_days <= 1:
        return 0.2
    if age_days <= 7:
        return 0.15
    if age_days <= 30:
        return 0.08
    if age_days <= 180:
        return 0.03
    return 0.0


MEMORY_BUCKETS: Tuple[str, ...] = tuple(store.MEMORY_TABLES)


def _empty_results() -> Dict[str, List[Dict[str, Any]]]:
    return {bucket: [] for bucket in MEMORY_BUCKETS}


def _parse_metadata(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


def _governance_state(metadata: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    preview = provenance.preview_from_metadata(metadata)
    prov = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
    state = {
        "memory_status": prov.get("memory_status") or metadata.get("memory_status") or "active",
        "superseded_by": prov.get("superseded_by") or metadata.get("superseded_by"),
        "supersedes": prov.get("supersedes") or metadata.get("supersedes"),
        "duplicate_of": prov.get("duplicate_of") or metadata.get("duplicate_of"),
        "contradicts": prov.get("contradicts") or metadata.get("contradicts") or [],
        "contradiction_status": prov.get("contradiction_status") or metadata.get("contradiction_status"),
        "canonical_reference": prov.get("canonical_reference") or metadata.get("canonical_reference"),
        "provenance_preview": preview,
    }
    return str(state["memory_status"] or "active"), state


def _governance_summary(governance: Dict[str, Any]) -> Dict[str, Any]:
    memory_status = str(governance.get("memory_status") or "active")
    duplicate_of = governance.get("duplicate_of")
    superseded_by = governance.get("superseded_by")
    supersedes = governance.get("supersedes")
    contradicts = governance.get("contradicts") if isinstance(governance.get("contradicts"), list) else []
    contradiction_status = governance.get("contradiction_status")
    needs_review = bool(memory_status == "contested" or contradiction_status == "contested" or contradicts)
    return {
        "memory_status": memory_status,
        "canonical_reference": governance.get("canonical_reference") or duplicate_of,
        "duplicate_of": duplicate_of,
        "superseded_by": superseded_by,
        "supersedes": supersedes,
        "contradiction_status": contradiction_status,
        "contradiction_count": len([item for item in contradicts if item]),
        "needs_review": needs_review,
    }


def _flatten_strings(value: Any) -> List[str]:
    items: List[str] = []
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            items.append(stripped)
    elif isinstance(value, dict):
        for child in value.values():
            items.extend(_flatten_strings(child))
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            items.extend(_flatten_strings(child))
    return items


def _metadata_lookup(metadata: Dict[str, Any], dotted_key: str) -> Any:
    current: Any = metadata
    for part in (dotted_key or "").split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _metadata_matches(metadata: Dict[str, Any], filters: Optional[Dict[str, Any]]) -> bool:
    if not filters:
        return True
    for key, expected in filters.items():
        actual = _metadata_lookup(metadata, key)
        actual_values = {item.lower() for item in _flatten_strings(actual)}
        expected_values = {item.lower() for item in _flatten_strings(expected)}
        if expected_values:
            if not actual_values.intersection(expected_values):
                return False
        else:
            if actual not in (None, "", [], {}):
                return False
    return True


def _load_lane_profiles() -> Dict[str, Dict[str, Any]]:
    raw = os.getenv("OCMEMOG_MEMORY_LANES_JSON", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    profiles: Dict[str, Dict[str, Any]] = {}
    for lane_name, config in payload.items():
        if not isinstance(config, dict):
            continue
        normalized_name = str(lane_name or "").strip().lower()
        if not normalized_name:
            continue
        profiles[normalized_name] = {
            "keywords": [item.lower() for item in _flatten_strings(config.get("keywords"))],
            "metadata_filters": config.get("metadata_filters") if isinstance(config.get("metadata_filters"), dict) else {},
        }
    return profiles


def infer_lane(prompt: str, explicit_lane: Optional[str] = None) -> Optional[str]:
    lane = str(explicit_lane or "").strip().lower()
    if lane:
        return lane
    profiles = _load_lane_profiles()
    if not profiles:
        return None
    prompt_l = str(prompt or "").lower()
    tokens = set(_tokenize(prompt))
    best_lane: Optional[str] = None
    best_score = 0
    for lane_name, config in profiles.items():
        keywords = {item for item in config.get("keywords", []) if item}
        if not keywords:
            continue
        score = 0
        for keyword in keywords:
            keyword_tokens = set(_tokenize(keyword))
            if not keyword_tokens:
                continue
            if len(keyword_tokens) == 1:
                if next(iter(keyword_tokens)) in tokens:
                    score += 1
            elif keyword.lower() in prompt_l:
                score += len(keyword_tokens)
        if score > best_score:
            best_score = score
            best_lane = lane_name
    return best_lane if best_score > 0 else None


def _lane_bonus(metadata: Dict[str, Any], lane: Optional[str]) -> float:
    lane_value = str(lane or "").strip().lower()
    if not lane_value:
        return 0.0
    domain = str(_metadata_lookup(metadata, "domain") or "").strip().lower()
    if domain == lane_value:
        return 0.2
    profile = _load_lane_profiles().get(lane_value) or {}
    filters = profile.get("metadata_filters") if isinstance(profile.get("metadata_filters"), dict) else {}
    if filters and _metadata_matches(metadata, filters):
        return 0.16
    source_labels = {item.lower() for item in _flatten_strings(_metadata_lookup(metadata, "source_labels"))}
    if lane_value in source_labels:
        return 0.08
    return 0.0


def get_last_retrieval_diagnostics() -> Dict[str, Any]:
    return dict(_LAST_RETRIEVAL_DIAGNOSTICS)


def retrieve(
    prompt: str,
    limit: int = 5,
    categories: Iterable[str] | None = None,
    *,
    skip_vector_provider: bool = False,
    metadata_filters: Optional[Dict[str, Any]] = None,
    lane: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    global _LAST_RETRIEVAL_DIAGNOSTICS
    emit_event(state_store.report_log_path(), "brain_memory_retrieval_start", status="ok")
    emit_event(state_store.report_log_path(), "brain_memory_retrieval_rank_start", status="ok")

    conn = store.connect()
    results = _empty_results()
    selected_categories = tuple(dict.fromkeys(category for category in (categories or MEMORY_BUCKETS) if category in MEMORY_BUCKETS))
    active_lane = infer_lane(prompt, explicit_lane=lane)
    _LAST_RETRIEVAL_DIAGNOSTICS = {
        "suppressed_by_governance": {
            "superseded": 0,
            "duplicate": 0,
        },
        "selected_categories": list(selected_categories),
    }

    reinf_rows = conn.execute("SELECT memory_reference, reward_score, confidence FROM experiences").fetchall()
    reinforcement: Dict[str, Dict[str, float]] = {}
    for row in reinf_rows:
        reference = str(row[0] or "")
        if not reference:
            continue
        current = reinforcement.setdefault(reference, {"reward_score": 0.0, "confidence": 0.0, "count": 0.0})
        current["reward_score"] += float(row[1] or 0.0)
        current["confidence"] += float(row[2] or 0.0)
        current["count"] += 1.0
    for current in reinforcement.values():
        count = max(1.0, float(current.get("count") or 1.0))
        current["reward_score"] = float(current.get("reward_score") or 0.0) / count
        current["confidence"] = float(current.get("confidence") or 0.0) / count

    semantic_scores: Dict[str, float] = {}
    if prompt.strip():
        for item in vector_index.search_memory(
            prompt,
            limit=max(limit * 6, 20),
            skip_provider=skip_vector_provider,
            source_types=selected_categories,
        ):
            source_type = item.get("source_type") or "knowledge"
            source_id = str(item.get("source_id") or "")
            if source_type in selected_categories and source_id:
                semantic_scores[f"{source_type}:{source_id}"] = float(item.get("score") or 0.0)

    def score_record(*, content: str, memory_ref: str, promo_conf: float, timestamp: str | None, metadata_payload: Dict[str, Any]) -> tuple[float, Dict[str, float]]:
        keyword = _match_score(content, prompt)
        semantic = float(semantic_scores.get(memory_ref, 0.0))
        reinf = reinforcement.get(memory_ref, {})
        reinf_score = float(reinf.get("reward_score", 0.0)) * 0.35
        promo_score = float(promo_conf) * 0.2
        recency = _recency_score(timestamp)
        lane_bonus = _lane_bonus(metadata_payload, active_lane)
        score = round((keyword * 0.45) + (semantic * 0.35) + reinf_score + promo_score + recency + lane_bonus, 3)
        return score, {
            "keyword": round(keyword, 3),
            "semantic": round(semantic, 3),
            "reinforcement": round(reinf_score, 3),
            "promotion": round(promo_score, 3),
            "recency": round(recency, 3),
            "lane_bonus": round(lane_bonus, 3),
        }

    for table in selected_categories:
        candidates: Dict[str, Dict[str, Any]] = {}
        try:
            rows = conn.execute(
                f"SELECT id, timestamp, content, confidence, metadata_json FROM {table} ORDER BY id DESC LIMIT ?",
                (max(limit * 20, 50),),
            ).fetchall()
        except Exception:
            continue
        for row in rows:
            content = row["content"] if isinstance(row, dict) else row[2]
            mem_ref = f"{table}:{row[0]}"
            keyword = _match_score(content, prompt)
            semantic = float(semantic_scores.get(mem_ref, 0.0))
            if prompt.strip() and keyword <= 0.0 and semantic <= 0.0:
                continue
            promo_conf = row["confidence"] if isinstance(row, dict) else row[3]
            timestamp = row["timestamp"] if isinstance(row, dict) else row[1]
            raw_metadata = row["metadata_json"] if isinstance(row, dict) else row[4]
            metadata_payload = _parse_metadata(raw_metadata)
            if not _metadata_matches(metadata_payload, metadata_filters):
                continue
            memory_status, governance = _governance_state(metadata_payload)
            if memory_status in {"superseded", "duplicate"}:
                _LAST_RETRIEVAL_DIAGNOSTICS["suppressed_by_governance"][memory_status] = (
                    int(_LAST_RETRIEVAL_DIAGNOSTICS["suppressed_by_governance"].get(memory_status) or 0) + 1
                )
                continue
            metadata = provenance.fetch_reference(mem_ref)
            score, signals = score_record(content=content, memory_ref=mem_ref, promo_conf=promo_conf, timestamp=timestamp, metadata_payload=metadata_payload)
            if memory_status == "contested":
                score = round(max(0.0, score - 0.15), 3)
                signals["contradiction_penalty"] = 0.15
            selected_because = max(signals, key=signals.get) if signals else "keyword"
            candidates[mem_ref] = {
                "content": content,
                "score": score,
                "memory_reference": mem_ref,
                "links": memory_links.get_memory_links(mem_ref),
                "provenance_preview": (metadata or {}).get("provenance_preview") or governance.get("provenance_preview") or provenance.preview_from_metadata((metadata or {}).get("metadata")),
                "retrieval_signals": signals,
                "selected_because": selected_because,
                "timestamp": timestamp,
                "memory_status": memory_status,
                "governance": governance,
                "governance_summary": _governance_summary(governance),
            }
        results[table] = sorted(candidates.values(), key=lambda x: x["score"], reverse=True)[:limit]

    conn.close()
    emit_event(state_store.report_log_path(), "brain_memory_retrieval_rank_complete", status="ok")
    emit_event(state_store.report_log_path(), "brain_memory_retrieval_complete", status="ok")
    return results


def retrieve_for_queries(
    queries: Iterable[str],
    *,
    limit: int = 5,
    categories: Iterable[str] | None = None,
    skip_vector_provider: bool = False,
    metadata_filters: Optional[Dict[str, Any]] = None,
    lane: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    merged = _empty_results()
    seen_refs = {bucket: set() for bucket in MEMORY_BUCKETS}
    selected_categories = tuple(dict.fromkeys(category for category in (categories or MEMORY_BUCKETS) if category in MEMORY_BUCKETS))
    normalized_queries = [query.strip() for query in queries if isinstance(query, str) and query.strip()]

    if not normalized_queries:
        return retrieve("", limit=limit, categories=selected_categories, metadata_filters=metadata_filters, lane=lane)

    for query in normalized_queries:
        partial = retrieve(
            query,
            limit=limit,
            categories=selected_categories,
            skip_vector_provider=skip_vector_provider,
            metadata_filters=metadata_filters,
            lane=lane,
        )
        for bucket in selected_categories:
            for item in partial.get(bucket, []):
                ref = item.get("memory_reference")
                if ref in seen_refs[bucket]:
                    continue
                seen_refs[bucket].add(ref)
                merged[bucket].append(item)

    for bucket in selected_categories:
        merged[bucket] = sorted(merged[bucket], key=lambda x: x["score"], reverse=True)[:limit]
    return merged
