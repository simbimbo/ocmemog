from __future__ import annotations

from typing import Any, Dict

from ocmemog.runtime.memory import integrity, store

EMBED_TABLES = tuple(store.MEMORY_TABLES)


def get_memory_health() -> Dict[str, Any]:
    conn = store.connect()
    counts: Dict[str, int] = {}
    for table in ["experiences", "candidates", "promotions", "memory_index", *store.MEMORY_TABLES, "vector_embeddings"]:
        try:
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except Exception:
            counts[table] = 0

    vector_index_count = 0
    vector_covered_sources = 0
    try:
        for table in EMBED_TABLES:
            vector_index_count += conn.execute(
                "SELECT COUNT(*) FROM vector_embeddings WHERE source_type=?",
                (table,),
            ).fetchone()[0]
            vector_covered_sources += conn.execute(
                f"""
                SELECT COUNT(*) FROM {table} AS source
                WHERE EXISTS (
                    SELECT 1
                    FROM vector_embeddings AS embeddings
                    WHERE embeddings.source_type = ?
                      AND CAST(embeddings.source_id AS TEXT) = CAST(source.id AS TEXT)
                )
                """,
                (table,),
            ).fetchone()[0]
    except Exception:
        vector_covered_sources = 0
        vector_index_count = 0

    total_embed_sources = sum(counts.get(table, 0) for table in EMBED_TABLES)
    conn.close()
    integrity_result = integrity.run_integrity_check()

    coverage = 0.0
    if total_embed_sources:
        coverage = round(vector_covered_sources / total_embed_sources, 3)

    return {
        "counts": counts,
        "vector_index_count": vector_index_count,
        "vector_index_coverage": coverage,
        "vector_index_integrity_status": integrity_result.get("ok"),
        "integrity": integrity_result,
    }
