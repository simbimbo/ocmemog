from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Any, Iterable, Tuple

import json

from ocmemog.runtime import state_store
from ocmemog.runtime.instrumentation import emit_event
from . import memory_links, provenance, store, vector_index


def _tokenize(text: str) -> List[str]:
    return [token for token in "".join(ch.lower() if ch.isalnum() else " " for ch in (text or "")).split() if token]


def _match_score(text: str, query: str) -> float:
    if not text or not query:
        return 0.0
    text_l = text.lower()
    query_l = query.lower()
    if query_l in text_l:
        return 1.0
    query_tokens = set(_tokenize(query_l))
    if not query_tokens:
        return 0.0
    text_tokens = set(_tokenize(text_l))
    if not text_tokens:
        return 0.0
    overlap = len(query_tokens & text_tokens) / max(1, len(query_tokens))
    return round(min(0.95, overlap * 0.85), 3)


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


def retrieve(
    prompt: str,
    limit: int = 5,
    categories: Iterable[str] | None = None,
    *,
    skip_vector_provider: bool = False,
) -> Dict[str, List[Dict[str, Any]]]:
    emit_event(state_store.report_log_path(), "brain_memory_retrieval_start", status="ok")
    emit_event(state_store.report_log_path(), "brain_memory_retrieval_rank_start", status="ok")

    conn = store.connect()
    results = _empty_results()
    selected_categories = tuple(dict.fromkeys(category for category in (categories or MEMORY_BUCKETS) if category in MEMORY_BUCKETS))

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

    def score_record(*, content: str, memory_ref: str, promo_conf: float, timestamp: str | None) -> tuple[float, Dict[str, float]]:
        keyword = _match_score(content, prompt)
        semantic = float(semantic_scores.get(memory_ref, 0.0))
        reinf = reinforcement.get(memory_ref, {})
        reinf_score = float(reinf.get("reward_score", 0.0)) * 0.35
        promo_score = float(promo_conf) * 0.2
        recency = _recency_score(timestamp)
        score = round((keyword * 0.45) + (semantic * 0.35) + reinf_score + promo_score + recency, 3)
        return score, {
            "keyword": round(keyword, 3),
            "semantic": round(semantic, 3),
            "reinforcement": round(reinf_score, 3),
            "promotion": round(promo_score, 3),
            "recency": round(recency, 3),
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
            memory_status, governance = _governance_state(metadata_payload)
            if memory_status in {"superseded", "duplicate"}:
                continue
            metadata = provenance.fetch_reference(mem_ref)
            score, signals = score_record(content=content, memory_ref=mem_ref, promo_conf=promo_conf, timestamp=timestamp)
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
) -> Dict[str, List[Dict[str, Any]]]:
    merged = _empty_results()
    seen_refs = {bucket: set() for bucket in MEMORY_BUCKETS}
    selected_categories = tuple(dict.fromkeys(category for category in (categories or MEMORY_BUCKETS) if category in MEMORY_BUCKETS))
    normalized_queries = [query.strip() for query in queries if isinstance(query, str) and query.strip()]

    if not normalized_queries:
        return retrieve("", limit=limit, categories=selected_categories)

    for query in normalized_queries:
        partial = retrieve(query, limit=limit, categories=selected_categories, skip_vector_provider=skip_vector_provider)
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
