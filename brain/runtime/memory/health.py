from __future__ import annotations

from typing import Dict, Any

from brain.runtime.memory import store, integrity


EMBED_TABLES = tuple(store.MEMORY_TABLES)


def get_memory_health() -> Dict[str, Any]:
    conn = store.connect()
    counts: Dict[str, int] = {}
    for table in ["experiences", "candidates", "promotions", "memory_index", *store.MEMORY_TABLES, "vector_embeddings"]:
        try:
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except Exception:
            counts[table] = 0

    vector_count = 0
    try:
        vector_count = conn.execute(
            "SELECT COUNT(*) FROM vector_embeddings WHERE source_type IN ('knowledge','preferences','identity','reflections','directives','tasks','runbooks','lessons')"
        ).fetchone()[0]
    except Exception:
        vector_count = 0

    total_embed_sources = sum(counts.get(table, 0) for table in EMBED_TABLES)
    conn.close()
    integrity_result = integrity.run_integrity_check()

    coverage = 0.0
    if total_embed_sources:
        coverage = round(vector_count / total_embed_sources, 3)

    return {
        "counts": counts,
        "vector_index_count": vector_count,
        "vector_index_coverage": coverage,
        "vector_index_integrity_status": integrity_result.get("ok"),
        "integrity": integrity_result,
    }
