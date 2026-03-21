from __future__ import annotations

from typing import Any, Dict, List

from ocmemog.runtime import state_store
from ocmemog.runtime.instrumentation import emit_event
from ocmemog.runtime.memory import embedding_engine, store, retrieval, freshness

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    size = min(len(a), len(b))
    if size == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a[:size], b[:size]))
    mag_a = sum(x * x for x in a[:size]) ** 0.5
    mag_b = sum(x * x for x in b[:size]) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def semantic_search(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    emit_event(LOGFILE, "brain_semantic_search_start", status="ok")
    query_embedding = embedding_engine.generate_embedding(query)
    conn = store.connect()
    rows = conn.execute(
        "SELECT id, source_type, source_id, embedding FROM vector_embeddings"
    ).fetchall()
    conn.close()

    reinforcement = retrieval.retrieve(query, limit=limit * 2)
    freshness_info = {item["memory_id"]: item for item in freshness.scan_freshness(limit=limit).get("advisories", [])}

    results: List[Dict[str, Any]] = []
    for row in rows:
        try:
            embedding = [float(x) for x in __import__("json").loads(row["embedding"])]
        except Exception:
            continue
        similarity = _cosine_similarity(query_embedding or [], embedding)
        memory_ref = f"{row['source_type']}:{row['source_id']}"
        reinforcement_weight = 0.0
        for bucket in reinforcement.values():
            for item in bucket:
                if item.get("memory_reference") == memory_ref:
                    reinforcement_weight = item.get("score", 0.0)
        freshness_score = freshness_info.get(int(row["source_id"],), {}).get("freshness_score", 0.0) if str(row["source_id"]).isdigit() else 0.0
        combined = similarity + reinforcement_weight + freshness_score
        results.append(
            {
                "memory_reference": memory_ref,
                "score": round(combined, 6),
                "similarity": round(similarity, 6),
                "freshness": freshness_score,
                "reinforcement_weight": reinforcement_weight,
                "promotion_confidence": 0.0,
            }
        )

    results.sort(key=lambda item: item["score"], reverse=True)
    emit_event(LOGFILE, "brain_semantic_search_complete", status="ok", result_count=len(results[:limit]))
    return results[:limit]
