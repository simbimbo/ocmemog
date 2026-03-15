from __future__ import annotations

from typing import Dict, List, Any, Iterable, Tuple

from brain.runtime.instrumentation import emit_event
from brain.runtime import state_store
from brain.runtime.memory import memory_links, provenance, store, vector_index


def _match_score(text: str, query: str) -> float:
    if not text:
        return 0.0
    text_l = text.lower()
    query_l = query.lower()
    if query_l in text_l:
        return 1.0
    return 0.0


MEMORY_BUCKETS: Tuple[str, ...] = (
    "knowledge",
    "reflections",
    "directives",
    "tasks",
    "runbooks",
    "lessons",
)


def _empty_results() -> Dict[str, List[Dict[str, Any]]]:
    return {bucket: [] for bucket in MEMORY_BUCKETS}


def retrieve(prompt: str, limit: int = 5, categories: Iterable[str] | None = None) -> Dict[str, List[Dict[str, Any]]]:
    emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_retrieval_start", status="ok")
    emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_retrieval_rank_start", status="ok")

    conn = store.connect()
    results = _empty_results()
    selected_categories = tuple(dict.fromkeys(category for category in (categories or MEMORY_BUCKETS) if category in MEMORY_BUCKETS))

    # reinforcement lookup (by memory_reference)
    reinf_rows = conn.execute(
        "SELECT memory_reference, reward_score, confidence FROM experiences",
    ).fetchall()
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

    def score_record(content: str, memory_ref: str, promo_conf: float) -> float:
        keyword = _match_score(content, prompt)
        reinf = reinforcement.get(memory_ref, {})
        reinf_score = float(reinf.get("reward_score", 0.0)) * 0.5
        promo_score = float(promo_conf) * 0.3
        return round(keyword + reinf_score + promo_score, 3)

    for table, key in [(bucket, bucket) for bucket in selected_categories]:
        try:
            rows = conn.execute(
                f"SELECT id, content, confidence, metadata_json FROM {table} ORDER BY id DESC LIMIT ?",
                (limit * 10,),
            ).fetchall()
        except Exception:
            continue
        for row in rows:
            content = row["content"] if isinstance(row, dict) else row[1]
            if not _match_score(content, prompt):
                continue
            mem_ref = f"{table}:{row[0]}"
            promo_conf = row["confidence"] if isinstance(row, dict) else row[2]
            metadata = provenance.fetch_reference(mem_ref)
            results[key].append({
                "content": content,
                "score": score_record(content, mem_ref, promo_conf),
                "memory_reference": mem_ref,
                "links": memory_links.get_memory_links(mem_ref),
                "provenance_preview": (metadata or {}).get("provenance_preview") or provenance.preview_from_metadata((metadata or {}).get("metadata")),
            })

        results[key] = sorted(results[key], key=lambda x: x["score"], reverse=True)[:limit]

    if prompt.strip() and all(not results.get(bucket) for bucket in selected_categories):
        semantic = vector_index.search_memory(prompt, limit=limit)
        for item in semantic:
            source_type = item.get("source_type") or "knowledge"
            if source_type not in selected_categories:
                continue
            try:
                row = conn.execute(
                    f"SELECT id, content, confidence, metadata_json FROM {source_type} WHERE id=?",
                    (int(item.get("source_id") or 0),),
                ).fetchone()
            except Exception:
                continue
            if not row:
                continue
            content = row["content"] if isinstance(row, dict) else row[1]
            mem_ref = f"{source_type}:{row[0]}"
            promo_conf = row["confidence"] if isinstance(row, dict) else row[2]
            metadata = provenance.fetch_reference(mem_ref)
            results[source_type].append({
                "content": content,
                "score": score_record(content, mem_ref, promo_conf),
                "memory_reference": mem_ref,
                "links": memory_links.get_memory_links(mem_ref),
                "provenance_preview": (metadata or {}).get("provenance_preview") or provenance.preview_from_metadata((metadata or {}).get("metadata")),
            })
        for bucket in selected_categories:
            results[bucket] = sorted(results[bucket], key=lambda x: x["score"], reverse=True)[:limit]

    conn.close()
    emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_retrieval_rank_complete", status="ok")
    emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_retrieval_complete", status="ok")
    return results


def retrieve_for_queries(
    queries: Iterable[str],
    *,
    limit: int = 5,
    categories: Iterable[str] | None = None,
) -> Dict[str, List[Dict[str, Any]]]:
    merged = _empty_results()
    seen_refs = {bucket: set() for bucket in MEMORY_BUCKETS}
    selected_categories = tuple(dict.fromkeys(category for category in (categories or MEMORY_BUCKETS) if category in MEMORY_BUCKETS))
    normalized_queries = [query.strip() for query in queries if isinstance(query, str) and query.strip()]

    if not normalized_queries:
        return retrieve("", limit=limit, categories=selected_categories)

    for query in normalized_queries:
        partial = retrieve(query, limit=limit, categories=selected_categories)
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
