from __future__ import annotations

import hashlib
import json
import math
import re
import os
import threading
from typing import Any, Dict, List, Iterable

from ocmemog.runtime.security import redaction
from ocmemog.runtime import state_store
from ocmemog.runtime.instrumentation import emit_event
from . import embedding_engine, memory_links, store

LOGFILE = state_store.report_log_path()

EMBEDDING_TABLES: tuple[str, ...] = tuple(store.MEMORY_TABLES)
_REBUILD_LOCK = threading.Lock()
_WRITE_CHUNK_SIZE = 64
_EMBEDDING_TEXT_LIMIT = 1000
_EMBEDDING_KNOWLEDGE_ARTIFACT_LIMIT = 500
_EMBEDDING_REFLECTION_LIMIT = 1200
_EMBEDDING_EXTENDED_LIMIT = 2000
_EMBEDDING_ULTRA_LIMIT = 4000
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_LAST_SEARCH_DIAGNOSTICS: Dict[str, Any] = {}


def _tokenize(text: str) -> List[str]:
    return [token for token in _WHITESPACE_RE.sub(" ", str(text or "").lower()).split(" ") if token]


def _lexical_overlap_score(query: str, content: str) -> float:
    query_tokens = {token for token in _tokenize(query) if len(token) >= 2}
    content_tokens = {token for token in _tokenize(content) if len(token) >= 2}
    if not query_tokens or not content_tokens:
        return 0.0
    overlap = len(query_tokens & content_tokens) / max(1, len(query_tokens))
    if overlap > 0.0:
        return round(min(1.0, overlap), 6)
    prefix_hits = 0
    sizable_query_tokens = [token for token in query_tokens if len(token) >= 4]
    if not sizable_query_tokens:
        return 0.0
    for token in sizable_query_tokens:
        if any(content_token.startswith(token) or token.startswith(content_token) for content_token in content_tokens):
            prefix_hits += 1
    if prefix_hits <= 0:
        return 0.0
    return round(min(0.75, prefix_hits / max(1, len(sizable_query_tokens))), 6)


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


def _normalized_tables(tables: Iterable[str] | None) -> List[str]:
    source = EMBEDDING_TABLES if tables is None else tables
    seen: set[str] = set()
    normalized: List[str] = []
    for table in source:
        if table in EMBEDDING_TABLES and table not in seen:
            normalized.append(table)
            seen.add(table)
    return normalized


def insert_memory(
    memory_id: int,
    content: str,
    confidence: float,
    *,
    source_type: str = "knowledge",
    skip_provider: bool = False,
) -> None:
    source_type = source_type if source_type in EMBEDDING_TABLES else "knowledge"
    redacted_content, changed = redaction.redact_text(content)
    embedding = embedding_engine.generate_embedding(redacted_content, skip_provider=skip_provider)
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
            where = (
                " WHERE NOT EXISTS ("
                "SELECT 1 FROM vector_embeddings AS ve "
                "WHERE ve.source_type = ? AND ve.source_id = CAST(tbl.id AS TEXT)"
                ")"
            )
            params.append(table)
        if limit is None:
            rows = conn.execute(
                f"SELECT tbl.id, tbl.content, tbl.confidence, tbl.metadata_json FROM {table} AS tbl{where} ORDER BY tbl.id {order}",
                tuple(params),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT tbl.id, tbl.content, tbl.confidence, tbl.metadata_json FROM {table} AS tbl{where} ORDER BY tbl.id {order} LIMIT ?",
                tuple(params + [limit]),
            ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _embedding_input(text: str, *, table: str = "knowledge") -> str:
    """Normalize and hard-cap embedding input text.

    Keep output deterministic and bounded for embedded calls that may have
    conservative token windows.
    """
    raw = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
    cleaned = _HTML_TAG_RE.sub(" ", raw)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    if not cleaned:
        cleaned = raw
    lowered = cleaned.lower()
    artifactish = (
        "| chunk " in lowered
        or ".sql" in lowered
        or "topology/" in lowered
        or cleaned.count("),(") >= 8
    )
    if table == "knowledge" and artifactish:
        return cleaned[:_EMBEDDING_KNOWLEDGE_ARTIFACT_LIMIT]
    if table == "knowledge" and len(cleaned) > 9000:
        return cleaned[:_EMBEDDING_TEXT_LIMIT]
    if table == "reflections" and len(cleaned) > 8000:
        return cleaned[:_EMBEDDING_REFLECTION_LIMIT]
    if len(cleaned) > 20000:
        return cleaned[:_EMBEDDING_ULTRA_LIMIT]
    if len(cleaned) > 12000:
        return cleaned[:_EMBEDDING_EXTENDED_LIMIT]
    # Local llama.cpp embedding runtime currently rejects inputs above its effective
    # token window (~512 tokens physical batch). Keep a conservative character cap so
    # backfill and live embedding stay deterministic instead of failing with HTTP 500s.
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
    requested_tables = _normalized_tables(tables)
    if not requested_tables:
        emit_event(LOGFILE, "brain_memory_vector_rebuild_complete", status="skipped", reason="no_valid_tables")
        return 0
    if not _REBUILD_LOCK.acquire(blocking=False):
        emit_event(LOGFILE, "brain_memory_vector_rebuild_complete", status="skipped", reason="already_running")
        return 0
    count = 0
    try:
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
    requested_tables = _normalized_tables(tables)
    if not requested_tables:
        emit_event(LOGFILE, "brain_memory_vector_backfill_complete", status="skipped", reason="no_valid_tables")
        return 0
    if limit_per_table is not None and limit_per_table <= 0:
        emit_event(LOGFILE, "brain_memory_vector_backfill_complete", status="skipped", reason="invalid_limit")
        return 0
    if not _REBUILD_LOCK.acquire(blocking=False):
        emit_event(LOGFILE, "brain_memory_vector_backfill_complete", status="skipped", reason="already_running")
        return 0
    count = 0
    try:
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


def get_last_search_diagnostics() -> Dict[str, Any]:
    return dict(_LAST_SEARCH_DIAGNOSTICS)


def search_memory(
    query: str,
    limit: int = 5,
    *,
    skip_provider: bool = False,
    source_types: Iterable[str] | None = None,
) -> List[Dict[str, Any]]:
    global _LAST_SEARCH_DIAGNOSTICS
    emit_event(LOGFILE, "brain_memory_vector_search_start", status="ok")
    conn = store.connect()
    _ensure_vector_table(conn)

    query_embedding = embedding_engine.generate_embedding(query, skip_provider=skip_provider)
    results: List[Dict[str, Any]] = []

    try:
        scan_limit = int(os.environ.get("OCMEMOG_SEARCH_VECTOR_SCAN_LIMIT", 1200))
    except Exception:
        scan_limit = 1200
    if scan_limit <= 0:
        scan_limit = max(1, limit * 8)
    scan_limit = max(limit, scan_limit)

    try:
        lexical_prefilter_limit = int(os.environ.get("OCMEMOG_SEARCH_VECTOR_PREFILTER_LIMIT", 250))
    except Exception:
        lexical_prefilter_limit = 250
    if lexical_prefilter_limit < 0:
        lexical_prefilter_limit = 0
    lexical_prefilter_limit = min(scan_limit, max(limit, lexical_prefilter_limit)) if lexical_prefilter_limit else 0

    if source_types is None:
        filtered_source_types = tuple(EMBEDDING_TABLES)
    else:
        filtered_source_types = tuple(
            source_type
            for source_type in dict.fromkeys(source_type for source_type in source_types if source_type in EMBEDDING_TABLES)
        )

    _LAST_SEARCH_DIAGNOSTICS = {
        "scan_limit": int(scan_limit),
        "prefilter_limit": int(lexical_prefilter_limit),
        "source_types": list(filtered_source_types),
        "query_embedding_ready": bool(query_embedding),
        "scanned_rows": 0,
        "prefilter_hits": 0,
        "candidate_rows": 0,
        "result_count": 0,
        "used_memory_index_fallback": False,
    }
    if filtered_source_types:
        placeholders = ",".join("?" for _ in filtered_source_types)
        vector_query = (
            "SELECT id, source_type, source_id, embedding "
            f"FROM vector_embeddings WHERE source_type IN ({placeholders}) "
            "ORDER BY rowid DESC LIMIT ?"
        )
        scan_rows = (*filtered_source_types, scan_limit)
    else:
        vector_query = "SELECT id, source_type, source_id, embedding FROM vector_embeddings ORDER BY rowid DESC LIMIT ?"
        scan_rows = (scan_limit,)

    if query_embedding:
        rows = conn.execute(vector_query, scan_rows).fetchall()
        _LAST_SEARCH_DIAGNOSTICS["scanned_rows"] = len(rows)
        lexical_ranked: List[tuple[float, Any]] = []
        for row in rows:
            lexical_score = 0.0
            if lexical_prefilter_limit:
                source_ref = f"{row['source_type']}:{row['source_id']}"
                memory_row = conn.execute(
                    "SELECT content FROM memory_index WHERE source = ? ORDER BY id DESC LIMIT 1",
                    (source_ref,),
                ).fetchone()
                content = str((memory_row[0] if memory_row else "") or "")
                lexical_score = _lexical_overlap_score(query, content)
            lexical_ranked.append((lexical_score, row))

        candidate_rows = rows
        if lexical_prefilter_limit and lexical_ranked:
            lexical_hits = [row for score, row in sorted(lexical_ranked, key=lambda item: item[0], reverse=True) if score > 0.0]
            _LAST_SEARCH_DIAGNOSTICS["prefilter_hits"] = len(lexical_hits)
            if lexical_hits:
                candidate_rows = lexical_hits[:lexical_prefilter_limit]
            else:
                candidate_rows = rows[:lexical_prefilter_limit]

        _LAST_SEARCH_DIAGNOSTICS["candidate_rows"] = len(candidate_rows)
        scored: List[Dict[str, Any]] = []
        for row in candidate_rows:
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
        _LAST_SEARCH_DIAGNOSTICS["result_count"] = len(results)

    if not results:
        fallback_where = ""
        fallback_params: List[Any] = [f"%{query}%"]
        if filtered_source_types:
            patterns = [f"{source_type}:%" for source_type in filtered_source_types]
            fallback_where = f" AND ({' OR '.join(['source LIKE ?'] * len(patterns))})"
            fallback_params.extend(patterns)
        rows = conn.execute(
            f"SELECT id, source, content, confidence, metadata_json FROM memory_index WHERE content LIKE ?{fallback_where} ORDER BY id DESC LIMIT ?",
            tuple(fallback_params + [limit]),
        ).fetchall()
        _LAST_SEARCH_DIAGNOSTICS["used_memory_index_fallback"] = True
        _LAST_SEARCH_DIAGNOSTICS["candidate_rows"] = len(rows)
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
