from __future__ import annotations

from typing import Dict, Any

from brain.runtime.memory import store, integrity


def get_memory_health() -> Dict[str, Any]:
    conn = store.connect()
    counts = {}
    for table in ["experiences", "candidates", "promotions", "memory_index", "knowledge"]:
        try:
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except Exception:
            counts[table] = 0
    conn.close()
    integrity_result = integrity.run_integrity_check()
    vector_count = counts.get("memory_index", 0)
    knowledge_count = counts.get("knowledge", 0)
    coverage = 0.0
    if knowledge_count:
        coverage = round(vector_count / knowledge_count, 3)
    return {
        "counts": counts,
        "vector_index_count": vector_count,
        "vector_index_coverage": coverage,
        "vector_index_integrity_status": integrity_result.get("ok"),
        "integrity": integrity_result,
    }
