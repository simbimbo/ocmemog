from __future__ import annotations

import json
import math
from typing import Any, Dict, List

from brain.runtime import state_store
from brain.runtime.instrumentation import emit_event
from brain.runtime.memory import embedding_engine, store, memory_links
from brain.runtime.security import redaction

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def _ensure_vector_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vector_embeddings (
            id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            embedding TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vector_embeddings_source ON vector_embeddings (source_type, source_id)"
    )


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    size = min(len(a), len(b))
    if size == 0:
        return 0.0
    a2 = a[:size]
    b2 = b[:size]
    dot = sum(x * y for x, y in zip(a2, b2))
    mag_a = math.sqrt(sum(x * x for x in a2))
    mag_b = math.sqrt(sum(x * x for x in b2))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def insert_memory(memory_id: int, content: str, confidence: float) -> None:
    redacted_content, changed = redaction.redact_text(content)
    conn = store.connect()
    _ensure_vector_table(conn)

    conn.execute(
        "INSERT INTO memory_index (source, confidence, metadata_json, content, schema_version) VALUES (?, ?, ?, ?, ?)",
        (str(memory_id), confidence, json.dumps({"redacted": changed}), redacted_content, store.SCHEMA_VERSION),
    )

    embedding = embedding_engine.generate_embedding(redacted_content)
    if embedding:
        emit_event(LOGFILE, "brain_memory_embedding_generated", status="ok", source_id=str(memory_id))
        conn.execute(
            """
            INSERT INTO vector_embeddings (id, source_type, source_id, embedding)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET embedding=excluded.embedding
            """,
            (f"knowledge:{memory_id}", "knowledge", str(memory_id), json.dumps(embedding)),
        )

    conn.commit()
    conn.close()


def index_memory(limit: int = 100) -> int:
    emit_event(LOGFILE, "brain_memory_vector_index_start", status="ok")
    conn = store.connect()
    _ensure_vector_table(conn)
    rows = conn.execute(
        "SELECT id, content, confidence FROM knowledge ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    count = 0
    for row in rows:
        content = str(row["content"] or "")
        redacted_content, changed = redaction.redact_text(content)
        embedding = embedding_engine.generate_embedding(redacted_content)
        if not embedding:
            continue
        conn.execute(
            "UPDATE knowledge SET content=?, metadata_json=? WHERE id=?",
            (redacted_content, json.dumps({"redacted": changed}), row["id"]),
        )
        conn.execute(
            """
            INSERT INTO vector_embeddings (id, source_type, source_id, embedding)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET embedding=excluded.embedding
            """,
            (f"knowledge:{row['id']}", "knowledge", str(row["id"]), json.dumps(embedding)),
        )
        count += 1
    conn.commit()
    conn.close()
    emit_event(LOGFILE, "brain_memory_vector_index_complete", status="ok", indexed=count)
    return count


def search_memory(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    emit_event(LOGFILE, "brain_memory_vector_search_start", status="ok")
    conn = store.connect()
    _ensure_vector_table(conn)

    query_embedding = embedding_engine.generate_embedding(query)
    results: List[Dict[str, Any]] = []

    if query_embedding:
        rows = conn.execute("SELECT id, source_type, source_id, embedding FROM vector_embeddings").fetchall()
        scored: List[Dict[str, Any]] = []
        for row in rows:
            try:
                emb = json.loads(row["embedding"])
                emb_list = [float(x) for x in emb]
            except Exception:
                continue
            score = _cosine_similarity(query_embedding, emb_list)
            scored.append(
                {
                    "entry_id": row["id"],
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "score": round(score, 6),
                }
            )
        scored.sort(key=lambda item: item["score"], reverse=True)
        results = scored[:limit]

    if not results:
        rows = conn.execute(
            "SELECT id, source, content, confidence FROM memory_index WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        results = [
            {
                "entry_id": f"memory_index:{row['id']}",
                "source_type": "memory_index",
                "source_id": str(row["source"]),
                "score": float(row["confidence"] or 0.0),
                "content": str(row["content"] or "")[:240],
                "links": memory_links.get_memory_links(f"memory_index:{row['id']}")
            }
            for row in rows
        ]

    conn.close()
    emit_event(LOGFILE, "brain_memory_vector_search_complete", status="ok", result_count=len(results))
    return results
