from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from typing import Any, Dict, List, Iterable

from ocmemog.runtime import state_store
from ocmemog.runtime.instrumentation import emit_event
from ocmemog.runtime.memory import embedding_engine, memory_links, store
from ocmemog.runtime.security import redaction

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"

EMBEDDING_TABLES: tuple[str, ...] = tuple(store.MEMORY_TABLES)
_REBUILD_LOCK = threading.Lock()
_WRITE_CHUNK_SIZE = 64
_EMBEDDING_TEXT_LIMIT = 8000
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


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


def insert_memory(memory_id: int, content: str, confidence: float, *, source_type: str = "knowledge") -> None:
    source_type = source_type if source_type in EMBEDDING_TABLES else "knowledge"
    redacted_content, changed = redaction.redact_text(content)
    embedding = embedding_engine.generate_embedding(redacted_content)
    metadata_json = json.dumps({"redacted": changed, "source_type": source_type})

    def _write() -> None:
        conn = store.connect()
        try:
            _ensure_vector_table(conn)
            conn.execute(
                "INSERT INTO memory_index (source, confidence, metadata_json, content, schema_version) VALUES (?, ?, ?, ?, ?)",
                (
                    f"{source_type}:{memory_id}",
                    confidence,
                    metadata_json,
                    redacted_content,
                    store.SCHEMA_VERSION,
                ),
            )
            if embedding:
                emit_event(LOGFILE, "brain_memory_embedding_generated", status="ok", source_id=str(memory_id))
                conn.execute(
                    """
                    INSERT INTO vector_embeddings (id, source_type, source_id, embedding)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET embedding=excluded.embedding
                    """,
                    (f"{source_type}:{memory_id}", source_type, str(memory_id), json.dumps(embedding)),
                )
            conn.commit()
        finally:
            conn.close()

    store.submit_write(_write, timeout=30.0)


def _load_table_rows(table: str, *, limit: int | None = None, descending: bool = False, missing_only: bool = False) -> List[Dict[str, Any]]:
    conn = store.connect()
    try:
        order = "DESC" if descending else "ASC"
        where = ""
        params: list[Any] = []
        if missing_only:
            where = " WHERE CAST(id AS TEXT) NOT IN (SELECT source_id FROM vector_embeddings WHERE source_type = ?)"
            params.append(table)
        if limit is None:
            rows = conn.execute(
                f"SELECT id, content, confidence, metadata_json FROM {table}{where} ORDER BY id {order}",
                tuple(params),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT id, content, confidence, metadata_json FROM {table}{where} ORDER BY id {order} LIMIT ?",
                tuple(params + [limit]),
            ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _embedding_input(text: str, *, table: str = "knowledge") -> str:
    cleaned = _HTML_TAG_RE.sub(" ", text)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    lowered = cleaned.lower()
    artifactish = (
        "| chunk " in lowered
        or ".sql" in lowered
        or "topology/" in lowered
        or cleaned.count("),(") >= 8
    )
    if table == "knowledge" and artifactish:
        return cleaned[:500]
    if table == "knowledge" and len(cleaned) > 9000:
        return cleaned[:1000]
    if table == "reflections" and len(cleaned) > 8000:
        return cleaned[:1200]
    if len(cleaned) > 20000:
        return cleaned[:2000]
    if len(cleaned) > 12000:
        return cleaned[:4000]
    return cleaned[:_EMBEDDING_TEXT_LIMIT]


def _prepare_embedding_rows(rows: Iterable[Dict[str, Any]], *, table: str) -> List[Dict[str, Any]]:
    prepared: List[Dict[str, Any]] = []
    embedding_cache: Dict[str, List[float] | None] = {}
    for row in rows:
        content = str(row.get("content") or "")
        redacted_content, changed = redaction.redact_text(content)
        embedding_input = _embedding_input(redacted_content, table=table)
        cache_key = hashlib.sha256(embedding_input.encode("utf-8", errors="ignore")).hexdigest()
        if cache_key in embedding_cache:
            embedding = embedding_cache[cache_key]
        else:
            embedding = embedding_engine.generate_embedding(embedding_input)
            embedding_cache[cache_key] = embedding
        if not embedding:
            continue
        try:
            metadata = json.loads(row.get("metadata_json") or "{}")
        except Exception:
            metadata = {}
        metadata["redacted"] = changed
        prepared.append(
            {
                "id": int(row["id"]),
                "content": redacted_content,
                "confidence": float(row.get("confidence") or 0.0),
                "metadata_json": json.dumps(metadata),
                "embedding": json.dumps(embedding),
                "source_type": table,
            }
        )
    return prepared


def _write_embedding_chunk(table: str, rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0

    def _write() -> int:
        conn = store.connect()
        try:
            _ensure_vector_table(conn)
            for row in rows:
                conn.execute(
                    f"UPDATE {table} SET content=?, metadata_json=? WHERE id=?",
                    (row["content"], row["metadata_json"], row["id"]),
                )
                conn.execute(
                    """
                    INSERT INTO vector_embeddings (id, source_type, source_id, embedding)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET embedding=excluded.embedding
                    """,
                    (f"{table}:{row['id']}", table, str(row["id"]), row["embedding"]),
                )
            conn.commit()
            return len(rows)
        finally:
            conn.close()

    return int(store.submit_write(_write, timeout=60.0))


def index_memory(limit: int = 100, *, tables: Iterable[str] | None = None) -> int:
    emit_event(LOGFILE, "brain_memory_vector_index_start", status="ok")
    count = 0
    for table in (tables or EMBEDDING_TABLES):
        if table not in EMBEDDING_TABLES:
            continue
        prepared = _prepare_embedding_rows(_load_table_rows(table, limit=limit, descending=True), table=table)
        for offset in range(0, len(prepared), _WRITE_CHUNK_SIZE):
            count += _write_embedding_chunk(table, prepared[offset: offset + _WRITE_CHUNK_SIZE])
    emit_event(LOGFILE, "brain_memory_vector_index_complete", status="ok", indexed=count)
    return count


def rebuild_vector_index(*, tables: Iterable[str] | None = None) -> int:
    emit_event(LOGFILE, "brain_memory_vector_rebuild_start", status="ok")
    if not _REBUILD_LOCK.acquire(blocking=False):
        emit_event(LOGFILE, "brain_memory_vector_rebuild_complete", status="skipped", reason="already_running")
        return 0
    count = 0
    try:
        requested_tables = [table for table in (tables or EMBEDDING_TABLES) if table in EMBEDDING_TABLES]

        def _clear() -> None:
            conn = store.connect()
            try:
                _ensure_vector_table(conn)
                if requested_tables:
                    conn.executemany(
                        "DELETE FROM vector_embeddings WHERE source_type = ?",
                        [(table,) for table in requested_tables],
                    )
                conn.commit()
            finally:
                conn.close()

        store.submit_write(_clear, timeout=60.0)
        for table in requested_tables:
            prepared = _prepare_embedding_rows(_load_table_rows(table), table=table)
            for offset in range(0, len(prepared), _WRITE_CHUNK_SIZE):
                count += _write_embedding_chunk(table, prepared[offset: offset + _WRITE_CHUNK_SIZE])
    finally:
        _REBUILD_LOCK.release()
    emit_event(LOGFILE, "brain_memory_vector_rebuild_complete", status="ok", indexed=count)
    return count


def backfill_missing_vectors(*, tables: Iterable[str] | None = None, limit_per_table: int | None = None) -> int:
    emit_event(LOGFILE, "brain_memory_vector_backfill_start", status="ok")
    if not _REBUILD_LOCK.acquire(blocking=False):
        emit_event(LOGFILE, "brain_memory_vector_backfill_complete", status="skipped", reason="already_running")
        return 0
    count = 0
    try:
        requested_tables = [table for table in (tables or EMBEDDING_TABLES) if table in EMBEDDING_TABLES]
        for table in requested_tables:
            prepared = _prepare_embedding_rows(
                _load_table_rows(table, limit=limit_per_table, missing_only=True),
                table=table,
            )
            for offset in range(0, len(prepared), _WRITE_CHUNK_SIZE):
                count += _write_embedding_chunk(table, prepared[offset: offset + _WRITE_CHUNK_SIZE])
    finally:
        _REBUILD_LOCK.release()
    emit_event(LOGFILE, "brain_memory_vector_backfill_complete", status="ok", indexed=count)
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
            "SELECT id, source, content, confidence, metadata_json FROM memory_index WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        fallback_results: List[Dict[str, Any]] = []
        for row in rows:
            source_ref = str(row["source"] or "")
            source_type, _, source_id = source_ref.partition(":")
            canonical_type = source_type if source_type in EMBEDDING_TABLES else "knowledge"
            canonical_ref = f"{canonical_type}:{source_id}" if source_id else source_ref
            fallback_results.append(
                {
                    "entry_id": canonical_ref,
                    "source_type": canonical_type,
                    "source_id": source_id or str(row["id"]),
                    "score": float(row["confidence"] or 0.0),
                    "content": str(row["content"] or "")[:240],
                    "links": memory_links.get_memory_links(canonical_ref),
                }
            )
        results = fallback_results

    conn.close()
    emit_event(LOGFILE, "brain_memory_vector_search_complete", status="ok", result_count=len(results))
    return results
