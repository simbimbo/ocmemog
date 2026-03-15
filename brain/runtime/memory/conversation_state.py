from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from brain.runtime.memory import store, memory_links

_ALLOWED_MEMORY_TABLES = {"knowledge", "reflections", "directives", "tasks", "runbooks", "lessons", "candidates", "promotions"}


def record_turn(
    *,
    role: str,
    content: str,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    message_id: Optional[str] = None,
    transcript_path: Optional[str] = None,
    transcript_offset: Optional[int] = None,
    transcript_end_offset: Optional[int] = None,
    source: Optional[str] = None,
    timestamp: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    turn_role = (role or "unknown").strip().lower() or "unknown"
    turn_content = (content or "").strip()
    if not turn_content:
        raise ValueError("empty_turn_content")

    def _write() -> int:
        conn = store.connect()
        try:
            if timestamp:
                cur = conn.execute(
                    """
                    INSERT INTO conversation_turns (
                        timestamp, conversation_id, session_id, thread_id, message_id,
                        role, content, transcript_path, transcript_offset, transcript_end_offset,
                        source, metadata_json, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        timestamp,
                        conversation_id,
                        session_id,
                        thread_id,
                        message_id,
                        turn_role,
                        turn_content,
                        transcript_path,
                        transcript_offset,
                        transcript_end_offset,
                        source,
                        json.dumps(metadata or {}, ensure_ascii=False),
                        store.SCHEMA_VERSION,
                    ),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO conversation_turns (
                        conversation_id, session_id, thread_id, message_id,
                        role, content, transcript_path, transcript_offset, transcript_end_offset,
                        source, metadata_json, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        session_id,
                        thread_id,
                        message_id,
                        turn_role,
                        turn_content,
                        transcript_path,
                        transcript_offset,
                        transcript_end_offset,
                        source,
                        json.dumps(metadata or {}, ensure_ascii=False),
                        store.SCHEMA_VERSION,
                    ),
                )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    return int(store.submit_write(_write, timeout=30.0))


def _rows_to_turns(rows) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for row in rows:
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except Exception:
            meta = {}
        items.append(
            {
                "id": int(row["id"]),
                "reference": f"conversation_turns:{row['id']}",
                "timestamp": row["timestamp"],
                "conversation_id": row["conversation_id"],
                "session_id": row["session_id"],
                "thread_id": row["thread_id"],
                "message_id": row["message_id"],
                "role": row["role"],
                "content": row["content"],
                "transcript_path": row["transcript_path"],
                "transcript_offset": row["transcript_offset"],
                "transcript_end_offset": row["transcript_end_offset"],
                "source": row["source"],
                "metadata": meta,
            }
        )
    return items


def get_recent_turns(
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    filters = []
    params: List[Any] = []
    if thread_id:
        filters.append("thread_id = ?")
        params.append(thread_id)
    elif session_id:
        filters.append("session_id = ?")
        params.append(session_id)
    elif conversation_id:
        filters.append("conversation_id = ?")
        params.append(conversation_id)

    query = "SELECT * FROM conversation_turns"
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(min(max(limit, 1), 100))

    conn = store.connect()
    try:
        rows = conn.execute(query, tuple(params)).fetchall()
    finally:
        conn.close()
    return list(reversed(_rows_to_turns(rows)))


def get_turn_counts(
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Dict[str, int]:
    filters = []
    params: List[Any] = []
    if thread_id:
        filters.append("thread_id = ?")
        params.append(thread_id)
    elif session_id:
        filters.append("session_id = ?")
        params.append(session_id)
    elif conversation_id:
        filters.append("conversation_id = ?")
        params.append(conversation_id)

    where = f" WHERE {' AND '.join(filters)}" if filters else ""
    conn = store.connect()
    try:
        row = conn.execute(
            f"""
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN role='user' THEN 1 ELSE 0 END) AS user_count,
              SUM(CASE WHEN role='assistant' THEN 1 ELSE 0 END) AS assistant_count
            FROM conversation_turns{where}
            """,
            tuple(params),
        ).fetchone()
    finally:
        conn.close()

    return {
        "total": int(row["total"] or 0),
        "user": int(row["user_count"] or 0),
        "assistant": int(row["assistant_count"] or 0),
    }


def get_linked_memories(
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    targets: List[str] = []
    if thread_id:
        targets.append(f"thread:{thread_id}")
    if session_id:
        targets.append(f"session:{session_id}")
    if conversation_id:
        targets.append(f"conversation:{conversation_id}")
    if not targets:
        return []

    placeholders = ",".join("?" for _ in targets)
    conn = store.connect()
    try:
        memory_links._ensure_table(conn)
        rows = conn.execute(
            f"""
            SELECT source_reference, link_type, target_reference, created_at
            FROM memory_links
            WHERE target_reference IN ({placeholders})
            ORDER BY created_at DESC, source_reference DESC
            LIMIT ?
            """,
            (*targets, min(max(limit, 1), 100)),
        ).fetchall()

        items: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            source_reference = str(row["source_reference"])
            if source_reference in seen:
                continue
            table, sep, raw_id = source_reference.partition(":")
            if not sep or table not in _ALLOWED_MEMORY_TABLES or not raw_id.isdigit():
                continue
            memory_row = conn.execute(
                f"SELECT id, timestamp, content, metadata_json FROM {table} WHERE id = ?",
                (int(raw_id),),
            ).fetchone()
            if not memory_row:
                continue
            linked_rows = conn.execute(
                "SELECT link_type, target_reference FROM memory_links WHERE source_reference = ? ORDER BY created_at ASC",
                (source_reference,),
            ).fetchall()
            try:
                meta = json.loads(memory_row["metadata_json"] or "{}")
            except Exception:
                meta = {}
            items.append(
                {
                    "reference": source_reference,
                    "timestamp": memory_row["timestamp"],
                    "content": memory_row["content"],
                    "metadata": meta,
                    "links": [
                        {"link_type": linked_row["link_type"], "target_reference": linked_row["target_reference"]}
                        for linked_row in linked_rows
                    ],
                }
            )
            seen.add(source_reference)
        return items
    finally:
        conn.close()
