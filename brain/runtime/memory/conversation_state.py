from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from brain.runtime import state_store
from brain.runtime.instrumentation import emit_event
from brain.runtime.memory import store, memory_links, unresolved_state, memory_salience

_ALLOWED_MEMORY_TABLES = {"knowledge", "reflections", "directives", "tasks", "runbooks", "lessons", "candidates", "promotions"}
LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"
_COMMITMENT_RE = re.compile(
    r"\b(i(?:'m| am)? going to|i will|i'll|let me|i can(?:\s+now)?|next,? i(?:'ll| will)|i should be able to)\b",
    re.IGNORECASE,
)
_CHECKPOINT_EVERY = max(0, int(os.environ.get("OCMEMOG_CONVERSATION_CHECKPOINT_EVERY", "6") or "6"))
_MAX_STATE_TURNS = max(6, int(os.environ.get("OCMEMOG_CONVERSATION_STATE_TURNS", "24") or "24"))
_SHORT_REPLY_NORMALIZED = {
    "yes",
    "yeah",
    "yep",
    "sure",
    "ok",
    "okay",
    "do it",
    "go ahead",
    "sounds good",
    "lets do it",
    "let us do it",
}
_NEGATIVE_SHORT_REPLY_NORMALIZED = {"no", "nope", "not now", "dont", "do not"}


def _state_from_payload(
    state_payload: Dict[str, Any],
    *,
    conversation_id: Optional[str],
    session_id: Optional[str],
    thread_id: Optional[str],
) -> Dict[str, Any]:
    latest_user_turn = state_payload.get("latest_user_turn") or {}
    latest_assistant_turn = state_payload.get("latest_assistant_turn") or {}
    latest_user_ask = state_payload.get("latest_user_intent") or state_payload.get("latest_user_ask") or {}
    latest_checkpoint = state_payload.get("latest_checkpoint") or {}
    return {
        "id": None,
        "scope_type": _scope_parts(conversation_id=conversation_id, session_id=session_id, thread_id=thread_id)[0],
        "scope_id": _scope_parts(conversation_id=conversation_id, session_id=session_id, thread_id=thread_id)[1],
        "conversation_id": conversation_id,
        "session_id": session_id,
        "thread_id": thread_id,
        "latest_user_turn_id": latest_user_turn.get("id"),
        "latest_assistant_turn_id": latest_assistant_turn.get("id"),
        "latest_user_ask": latest_user_ask.get("effective_content") or latest_user_ask.get("content"),
        "last_assistant_commitment": (state_payload.get("last_assistant_commitment") or {}).get("content"),
        "open_loops": state_payload.get("open_loops") or [],
        "pending_actions": state_payload.get("pending_actions") or [],
        "unresolved_state": state_payload.get("unresolved_state") or [],
        "latest_checkpoint_id": latest_checkpoint.get("id"),
        "metadata": {
            "summary_text": state_payload.get("summary_text"),
            "active_branch": state_payload.get("active_branch"),
            "latest_user_intent": state_payload.get("latest_user_intent"),
            "state_status": "derived_not_persisted",
        },
        "updated_at": None,
    }



def _scope_parts(
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    if thread_id:
        return "thread", thread_id
    if session_id:
        return "session", session_id
    if conversation_id:
        return "conversation", conversation_id
    return None, None


def _scope_target_refs(
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> List[str]:
    refs: List[str] = []
    if thread_id:
        refs.append(f"thread:{thread_id}")
    if session_id:
        refs.append(f"session:{session_id}")
    if conversation_id:
        refs.append(f"conversation:{conversation_id}")
    return refs


def _scope_where(
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    upto_turn_id: Optional[int] = None,
) -> tuple[str, List[Any]]:
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
    if upto_turn_id is not None:
        filters.append("id <= ?")
        params.append(int(upto_turn_id))
    where = f" WHERE {' AND '.join(filters)}" if filters else ""
    return where, params


def _normalized_reply_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").strip().lower()).strip()


def _turn_meta(turn: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not turn:
        return {}
    meta = turn.get("metadata") or {}
    return dict(meta) if isinstance(meta, dict) else {}


def _is_ambiguous_short_reply(text: str) -> bool:
    normalized = _normalized_reply_text(text)
    if not normalized:
        return False
    tokens = normalized.split()
    if len(tokens) > 4 or len(normalized) > 24:
        return False
    return normalized in _SHORT_REPLY_NORMALIZED or normalized in _NEGATIVE_SHORT_REPLY_NORMALIZED


def _find_turn_by_message_id(
    message_id: Optional[str],
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    upto_turn_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if not message_id:
        return None
    where, params = _scope_where(
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
        upto_turn_id=upto_turn_id,
    )
    query = f"SELECT * FROM conversation_turns{where}{' AND ' if where else ' WHERE '}message_id = ? ORDER BY id DESC LIMIT 1"
    conn = store.connect()
    try:
        row = conn.execute(query, (*params, message_id)).fetchone()
    finally:
        conn.close()
    turns = _rows_to_turns([row] if row else [])
    return turns[0] if turns else None


def _get_turn_by_id(turn_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if not turn_id:
        return None
    conn = store.connect()
    try:
        row = conn.execute("SELECT * FROM conversation_turns WHERE id = ?", (int(turn_id),)).fetchone()
    finally:
        conn.close()
    turns = _rows_to_turns([row] if row else [])
    return turns[0] if turns else None


def _resolve_explicit_reply_target(
    metadata: Dict[str, Any],
    prior_turns: Sequence[Dict[str, Any]],
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    reply_to_turn_id = metadata.get("reply_to_turn_id")
    if isinstance(reply_to_turn_id, int):
        for turn in reversed(prior_turns):
            if int(turn.get("id") or 0) == reply_to_turn_id:
                return turn
        found = _get_turn_by_id(reply_to_turn_id)
        if found:
            return found
    reply_to_message_id = metadata.get("reply_to_message_id") or metadata.get("parent_message_id")
    if isinstance(reply_to_message_id, str) and reply_to_message_id.strip():
        for turn in reversed(prior_turns):
            if turn.get("message_id") == reply_to_message_id:
                return turn
        found = _find_turn_by_message_id(
            reply_to_message_id,
            conversation_id=conversation_id,
            session_id=session_id,
            thread_id=thread_id,
        )
        if found:
            return found
    return None


def _infer_short_reply_resolution(
    turn_content: str,
    prior_turns: Sequence[Dict[str, Any]],
    *,
    reply_target: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if not _is_ambiguous_short_reply(turn_content):
        return None
    normalized = _normalized_reply_text(turn_content)
    referent = reply_target
    if referent is None:
        referent = _assistant_commitment(prior_turns) or _latest_turn_by_role(prior_turns, "assistant")
    if not referent:
        return None
    referent_content = str(referent.get("content") or "").strip()
    if not referent_content:
        return None
    decision = "decline" if normalized in _NEGATIVE_SHORT_REPLY_NORMALIZED else "confirm"
    effective_summary = referent_content
    if decision == "confirm":
        effective_summary = f"User confirmed assistant proposal/question: {referent_content}"
    elif decision == "decline":
        effective_summary = f"User declined assistant proposal/question: {referent_content}"
    return {
        "kind": "short_reply_reference",
        "decision": decision,
        "reply_text": turn_content,
        "normalized_reply": normalized,
        "resolved_turn_id": referent.get("id"),
        "resolved_reference": referent.get("reference"),
        "resolved_message_id": referent.get("message_id"),
        "resolved_content": referent_content,
        "effective_summary": effective_summary,
    }


def _enrich_turn_metadata(
    *,
    role: str,
    content: str,
    conversation_id: Optional[str],
    session_id: Optional[str],
    thread_id: Optional[str],
    message_id: Optional[str],
    metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    enriched = dict(metadata or {})
    prior_turns = get_recent_turns(
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
        limit=max(8, min(_MAX_STATE_TURNS, 32)),
    )
    reply_target = _resolve_explicit_reply_target(
        enriched,
        prior_turns,
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
    )
    if reply_target is None and prior_turns:
        last_turn = prior_turns[-1]
        if role == "assistant" and last_turn.get("role") == "user":
            reply_target = last_turn
        elif role == "user" and last_turn.get("role") == "assistant":
            reply_target = last_turn
    resolution = None
    if role == "user":
        resolution = _infer_short_reply_resolution(content, prior_turns, reply_target=reply_target)
        if resolution:
            enriched["resolution"] = resolution
            if reply_target is None:
                reply_target = _get_turn_by_id(resolution.get("resolved_turn_id"))
    if reply_target:
        reply_meta = _turn_meta(reply_target)
        branch_root_turn_id = int(reply_meta.get("branch_root_turn_id") or reply_target.get("id") or 0) or None
        branch_id = str(reply_meta.get("branch_id") or f"branch:{branch_root_turn_id or reply_target.get('id')}")
        enriched["reply_to_turn_id"] = int(reply_target.get("id") or 0) or None
        enriched["reply_to_reference"] = reply_target.get("reference")
        if reply_target.get("message_id"):
            enriched["reply_to_message_id"] = reply_target.get("message_id")
        if branch_root_turn_id:
            enriched["branch_root_turn_id"] = branch_root_turn_id
        enriched["branch_id"] = branch_id
        enriched["branch_depth"] = int(reply_meta.get("branch_depth") or 0) + 1
    elif message_id and "branch_id" not in enriched:
        enriched["branch_id"] = f"message:{message_id}"
        enriched["branch_depth"] = 0
    return enriched


def _effective_turn_content(turn: Optional[Dict[str, Any]]) -> Optional[str]:
    if not turn:
        return None
    resolution = _turn_meta(turn).get("resolution") or {}
    effective = str(resolution.get("effective_summary") or "").strip()
    if effective:
        return effective
    return str(turn.get("content") or "").strip() or None


def _reply_chain_for_turn(turn: Optional[Dict[str, Any]], turns: Sequence[Dict[str, Any]], *, limit: int = 6) -> List[Dict[str, Any]]:
    if not turn:
        return []
    lookup = {int(item.get("id") or 0): item for item in turns if item.get("id") is not None}
    chain: List[Dict[str, Any]] = []
    current = turn
    seen: set[int] = set()
    while current and len(chain) < max(1, limit):
        anchor = _turn_anchor(current)
        if anchor:
            chain.append(anchor)
        reply_to_turn_id = _turn_meta(current).get("reply_to_turn_id")
        if not isinstance(reply_to_turn_id, int) or reply_to_turn_id in seen:
            break
        seen.add(reply_to_turn_id)
        current = lookup.get(reply_to_turn_id) or _get_turn_by_id(reply_to_turn_id)
    return list(reversed(chain))


def _active_branch_payload(turns: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    turns_list = list(turns)
    if not turns_list:
        return None
    latest_turn = turns_list[-1]
    latest_meta = _turn_meta(latest_turn)
    root_turn_id = int(latest_meta.get("branch_root_turn_id") or latest_turn.get("id") or 0) or None
    branch_id = str(latest_meta.get("branch_id") or f"turn:{latest_turn.get('id')}")
    branch_turns = [
        turn for turn in turns_list
        if str(_turn_meta(turn).get("branch_id") or f"turn:{turn.get('id')}") == branch_id
        or (root_turn_id and int(turn.get("id") or 0) == root_turn_id)
    ]
    if not branch_turns:
        branch_turns = [latest_turn]
    return {
        "branch_id": branch_id,
        "root_turn_id": root_turn_id or latest_turn.get("id"),
        "latest_turn": _turn_anchor(latest_turn),
        "turn_ids": [int(turn.get("id") or 0) for turn in branch_turns],
        "turns": [_turn_anchor(turn) for turn in branch_turns[-8:]],
        "reply_chain": _reply_chain_for_turn(latest_turn, turns_list, limit=8),
    }


def _ranked_turn_expansion(turns: Sequence[Dict[str, Any]], active_branch: Optional[Dict[str, Any]], *, limit: int = 12) -> List[Dict[str, Any]]:
    branch_id = str((active_branch or {}).get("branch_id") or "") or None
    reply_chain = active_branch.get("reply_chain") if isinstance(active_branch, dict) else []
    reply_chain_turn_ids = [int(item.get("id") or 0) for item in reply_chain if int(item.get("id") or 0) > 0]
    return memory_salience.rank_turns_by_salience(
        turns,
        active_branch_id=branch_id,
        reply_chain_turn_ids=reply_chain_turn_ids,
        limit=min(max(limit, 1), 50),
    )


def _ranked_checkpoint_expansion(
    checkpoints: Sequence[Dict[str, Any]],
    active_branch: Optional[Dict[str, Any]],
    *,
    limit: int = 12,
) -> List[Dict[str, Any]]:
    branch_id = str((active_branch or {}).get("branch_id") or "") or None
    return memory_salience.rank_checkpoints_by_salience(
        checkpoints,
        active_branch_id=branch_id,
        limit=min(max(limit, 1), 50),
    )


def _checkpoint_scope_filter(checkpoint: Dict[str, Any]) -> Dict[str, Optional[str]]:
    return {
        "conversation_id": checkpoint.get("conversation_id"),
        "session_id": checkpoint.get("session_id"),
        "thread_id": checkpoint.get("thread_id"),
    }


def _get_turns_between_ids(
    start_id: int,
    end_id: int,
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    if end_id < start_id:
        return []
    where, params = _scope_where(
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
    )
    query = f"SELECT * FROM conversation_turns{where}{' AND ' if where else ' WHERE '}id BETWEEN ? AND ? ORDER BY id ASC LIMIT ?"
    conn = store.connect()
    try:
        rows = conn.execute(query, (*params, int(start_id), int(end_id), min(max(limit, 1), 500))).fetchall()
    finally:
        conn.close()
    return _rows_to_turns(rows)


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
    enriched_metadata = _enrich_turn_metadata(
        role=turn_role,
        content=turn_content,
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
        message_id=message_id,
        metadata=metadata,
    )

    def _write() -> int:
        conn = store.connect()
        try:
            if message_id:
                row = conn.execute(
                    """
                    SELECT id, metadata_json, transcript_path, transcript_offset, transcript_end_offset
                    FROM conversation_turns
                    WHERE role = ? AND message_id = ?
                      AND COALESCE(conversation_id, '') = COALESCE(?, '')
                      AND COALESCE(session_id, '') = COALESCE(?, '')
                      AND COALESCE(thread_id, '') = COALESCE(?, '')
                    ORDER BY id DESC LIMIT 1
                    """,
                    (turn_role, message_id, conversation_id, session_id, thread_id),
                ).fetchone()
                if row is not None:
                    try:
                        existing_meta = json.loads(row["metadata_json"] or "{}")
                    except Exception:
                        existing_meta = {}
                    merged_meta = {**existing_meta, **enriched_metadata}
                    conn.execute(
                        """
                        UPDATE conversation_turns
                        SET content = ?,
                            transcript_path = COALESCE(?, transcript_path),
                            transcript_offset = COALESCE(?, transcript_offset),
                            transcript_end_offset = COALESCE(?, transcript_end_offset),
                            source = COALESCE(?, source),
                            metadata_json = ?
                        WHERE id = ?
                        """,
                        (
                            turn_content,
                            transcript_path,
                            transcript_offset,
                            transcript_end_offset,
                            source,
                            json.dumps(merged_meta, ensure_ascii=False),
                            int(row["id"]),
                        ),
                    )
                    conn.commit()
                    return int(row["id"])
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
                        json.dumps(enriched_metadata, ensure_ascii=False),
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
                        json.dumps(enriched_metadata, ensure_ascii=False),
                        store.SCHEMA_VERSION,
                    ),
                )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    turn_id = int(store.submit_write(_write, timeout=30.0))
    try:
        refresh_state(
            conversation_id=conversation_id,
            session_id=session_id,
            thread_id=thread_id,
        )
        if _CHECKPOINT_EVERY > 0:
            counts = get_turn_counts(conversation_id=conversation_id, session_id=session_id, thread_id=thread_id)
            if counts["total"] > 0 and counts["total"] % _CHECKPOINT_EVERY == 0:
                latest = get_latest_checkpoint(conversation_id=conversation_id, session_id=session_id, thread_id=thread_id)
                if not latest or int(latest.get("turn_end_id") or 0) < turn_id:
                    create_checkpoint(
                        conversation_id=conversation_id,
                        session_id=session_id,
                        thread_id=thread_id,
                        upto_turn_id=turn_id,
                        checkpoint_kind="rolling",
                    )
    except Exception as exc:
        emit_event(
            LOGFILE,
            "brain_conversation_turn_post_write_maintenance_failed",
            status="warn",
            error=str(exc),
            turn_id=turn_id,
        )
    emit_event(LOGFILE, "brain_conversation_turn_recorded", status="ok", role=turn_role, turn_id=turn_id)
    return turn_id


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
    upto_turn_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    where, params = _scope_where(
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
        upto_turn_id=upto_turn_id,
    )
    query = f"SELECT * FROM conversation_turns{where} ORDER BY id DESC LIMIT ?"
    params.append(min(max(limit, 1), 200))

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
    where, params = _scope_where(
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
    )
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
    targets = _scope_target_refs(
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
    )
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


def _latest_turn_by_role(turns: Sequence[Dict[str, Any]], role: str) -> Optional[Dict[str, Any]]:
    return next((turn for turn in reversed(turns) if turn.get("role") == role), None)


def _assistant_commitment(turns: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for turn in reversed(turns):
        if turn.get("role") != "assistant":
            continue
        content = str(turn.get("content") or "").strip()
        if _COMMITMENT_RE.search(content):
            return turn
    return None


def _assistant_question(turns: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for turn in reversed(turns):
        if turn.get("role") != "assistant":
            continue
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        if "?" in content or any(token in content.lower() for token in ("let me know", "which do you want", "should i", "want me to")):
            return turn
    return None


def _looks_complete(text: str) -> bool:
    normalized = (text or "").lower()
    return any(token in normalized for token in ("done", "completed", "finished", "shipped", "implemented", "added", "fixed", "sent"))


def _has_later_assistant_turn(turns: Sequence[Dict[str, Any]], turn_id: int) -> bool:
    return any(turn.get("role") == "assistant" and int(turn.get("id") or 0) > turn_id for turn in turns)


def _latest_unresolved_commitment(turns: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for turn in reversed(turns):
        if turn.get("role") != "assistant":
            continue
        content = str(turn.get("content") or "").strip()
        if not content or not _COMMITMENT_RE.search(content):
            continue
        turn_id = int(turn.get("id") or 0)
        if not _has_later_assistant_turn(turns, turn_id):
            return turn
        break
    return None


def _turn_anchor(turn: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not turn:
        return None
    meta = _turn_meta(turn)
    return {
        "id": turn.get("id"),
        "reference": turn.get("reference"),
        "message_id": turn.get("message_id"),
        "role": turn.get("role"),
        "timestamp": turn.get("timestamp"),
        "content": turn.get("content"),
        "effective_content": _effective_turn_content(turn),
        "transcript_path": turn.get("transcript_path"),
        "transcript_offset": turn.get("transcript_offset"),
        "transcript_end_offset": turn.get("transcript_end_offset"),
        "reply_to_turn_id": meta.get("reply_to_turn_id"),
        "reply_to_reference": meta.get("reply_to_reference"),
        "reply_to_message_id": meta.get("reply_to_message_id"),
        "branch_id": meta.get("branch_id"),
        "branch_root_turn_id": meta.get("branch_root_turn_id"),
        "resolution": meta.get("resolution"),
    }


def _pending_from_turns(turns: Sequence[Dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pending_actions: list[dict[str, Any]] = []
    open_loops: list[dict[str, Any]] = []
    last_user = _latest_turn_by_role(turns, "user")
    last_assistant = _latest_turn_by_role(turns, "assistant")
    commitment = _latest_unresolved_commitment(turns)
    assistant_question = _assistant_question(turns)

    if last_user and (not last_assistant or int(last_user.get("id") or 0) > int(last_assistant.get("id") or 0)):
        content = _effective_turn_content(last_user) or str(last_user.get("content") or "").strip()
        resolution = _turn_meta(last_user).get("resolution") or {}
        if content:
            open_loops.append(
                {
                    "kind": "awaiting_assistant_reply",
                    "summary": content,
                    "source_reference": last_user.get("reference"),
                    "related_reference": resolution.get("resolved_reference"),
                }
            )
            pending_actions.append(
                {
                    "kind": "respond_to_latest_user",
                    "summary": content,
                    "source_reference": last_user.get("reference"),
                    "related_reference": resolution.get("resolved_reference"),
                }
            )
            if resolution:
                pending_actions.append(
                    {
                        "kind": "fulfill_confirmed_branch",
                        "summary": content,
                        "source_reference": last_user.get("reference"),
                        "related_reference": resolution.get("resolved_reference"),
                    }
                )

    if assistant_question and (not last_user or int(assistant_question.get("id") or 0) > int(last_user.get("id") or 0)):
        content = str(assistant_question.get("content") or "").strip()
        if content:
            open_loops.append(
                {
                    "kind": "awaiting_user_reply",
                    "summary": content,
                    "source_reference": assistant_question.get("reference"),
                }
            )
            pending_actions.append(
                {
                    "kind": "await_user_clarification",
                    "summary": content,
                    "source_reference": assistant_question.get("reference"),
                }
            )

    if commitment:
        content = str(commitment.get("content") or "").strip()
        if content and not _looks_complete(content):
            pending_actions.append(
                {
                    "kind": "assistant_commitment",
                    "summary": content,
                    "source_reference": commitment.get("reference"),
                }
            )
            open_loops.append(
                {
                    "kind": "assistant_commitment",
                    "summary": content,
                    "source_reference": commitment.get("reference"),
                }
            )

    deduped_pending: list[dict[str, Any]] = []
    seen = set()
    for item in pending_actions:
        key = (item.get("kind"), item.get("summary"), item.get("source_reference"), item.get("related_reference"))
        if key in seen:
            continue
        seen.add(key)
        deduped_pending.append(item)

    deduped_loops: list[dict[str, Any]] = []
    seen = set()
    for item in open_loops:
        key = (item.get("kind"), item.get("summary"), item.get("source_reference"), item.get("related_reference"))
        if key in seen:
            continue
        seen.add(key)
        deduped_loops.append(item)

    return deduped_pending, deduped_loops


def list_relevant_unresolved_state(
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    target_refs = set(
        _scope_target_refs(
            conversation_id=conversation_id,
            session_id=session_id,
            thread_id=thread_id,
        )
    )
    if not target_refs:
        return []
    return unresolved_state.list_unresolved_state_for_references(list(target_refs), limit=limit)


def infer_hydration_payload(
    turns: Sequence[Dict[str, Any]],
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    unresolved_items: Optional[Sequence[Dict[str, Any]]] = None,
    latest_checkpoint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    turns_list = list(turns)
    latest_user_turn = _latest_turn_by_role(turns_list, "user")
    latest_assistant_turn = _latest_turn_by_role(turns_list, "assistant")
    latest_commitment_turn = _assistant_commitment(turns_list)
    last_turn = turns_list[-1] if turns_list else None
    pending_actions, open_loops = _pending_from_turns(turns_list)
    unresolved_payload = list(unresolved_items or [])
    active_branch = _active_branch_payload(turns_list)
    checkpoint_lineage = get_checkpoint_lineage(latest_checkpoint.get("id")) if latest_checkpoint else []

    for item in unresolved_payload:
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        open_loops.append(
            {
                "kind": item.get("state_type") or "unresolved_state",
                "summary": summary,
                "source_reference": item.get("reference"),
                "state_id": item.get("state_id"),
            }
        )

    deduped_open_loops: list[dict[str, Any]] = []
    seen = set()
    for item in open_loops:
        key = (item.get("kind"), item.get("summary"), item.get("source_reference"), item.get("state_id"), item.get("related_reference"))
        if key in seen:
            continue
        seen.add(key)
        deduped_open_loops.append(item)

    summary_text_parts: list[str] = []
    if latest_checkpoint and latest_checkpoint.get("summary"):
        summary_text_parts.append(str(latest_checkpoint["summary"]).strip())
    if latest_user_turn:
        summary_text_parts.append(f"Latest user ask: {_effective_turn_content(latest_user_turn) or str(latest_user_turn.get('content') or '').strip()}")
    if latest_commitment_turn:
        summary_text_parts.append(f"Last assistant commitment: {str(latest_commitment_turn.get('content') or '').strip()}")
    summary_text = " | ".join(part for part in summary_text_parts if part)

    return {
        "turn_count": len(turns_list),
        "latest_user_turn": _turn_anchor(latest_user_turn),
        "latest_assistant_turn": _turn_anchor(latest_assistant_turn),
        "latest_user_ask": _turn_anchor(latest_user_turn),
        "latest_user_intent": {
            "literal": _turn_anchor(latest_user_turn),
            "effective_content": _effective_turn_content(latest_user_turn),
            "resolution": _turn_meta(latest_user_turn).get("resolution") if latest_user_turn else None,
        } if latest_user_turn else None,
        "last_assistant_commitment": _turn_anchor(latest_commitment_turn),
        "latest_transcript_anchor": {
            "path": last_turn.get("transcript_path") if last_turn else None,
            "start_line": last_turn.get("transcript_offset") if last_turn else None,
            "end_line": last_turn.get("transcript_end_offset") if last_turn else None,
        },
        "open_loops": deduped_open_loops,
        "pending_actions": pending_actions,
        "unresolved_state": unresolved_payload,
        "latest_checkpoint": latest_checkpoint,
        "checkpoint_graph": {
            "latest": latest_checkpoint,
            "lineage": checkpoint_lineage,
        } if latest_checkpoint else None,
        "active_branch": active_branch,
        "summary_text": summary_text,
        "summary_status": "derived",
        "scope": {
            "conversation_id": conversation_id,
            "session_id": session_id,
            "thread_id": thread_id,
        },
    }


def _upsert_state(
    *,
    conversation_id: Optional[str],
    session_id: Optional[str],
    thread_id: Optional[str],
    state_payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    scope_type, scope_id = _scope_parts(
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
    )
    if not scope_type or not scope_id:
        return None

    def _write() -> None:
        conn = store.connect()
        try:
            conn.execute(
                """
                INSERT INTO conversation_state (
                    scope_type, scope_id, conversation_id, session_id, thread_id,
                    latest_user_turn_id, latest_assistant_turn_id,
                    latest_user_ask, last_assistant_commitment,
                    open_loops_json, pending_actions_json, unresolved_state_json,
                    latest_checkpoint_id, metadata_json, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_type, scope_id) DO UPDATE SET
                    conversation_id=excluded.conversation_id,
                    session_id=excluded.session_id,
                    thread_id=excluded.thread_id,
                    latest_user_turn_id=excluded.latest_user_turn_id,
                    latest_assistant_turn_id=excluded.latest_assistant_turn_id,
                    latest_user_ask=excluded.latest_user_ask,
                    last_assistant_commitment=excluded.last_assistant_commitment,
                    open_loops_json=excluded.open_loops_json,
                    pending_actions_json=excluded.pending_actions_json,
                    unresolved_state_json=excluded.unresolved_state_json,
                    latest_checkpoint_id=excluded.latest_checkpoint_id,
                    metadata_json=excluded.metadata_json,
                    updated_at=datetime('now'),
                    schema_version=excluded.schema_version
                """,
                (
                    scope_type,
                    scope_id,
                    conversation_id,
                    session_id,
                    thread_id,
                    (state_payload.get("latest_user_turn") or {}).get("id"),
                    (state_payload.get("latest_assistant_turn") or {}).get("id"),
                    (state_payload.get("latest_user_ask") or {}).get("effective_content") or (state_payload.get("latest_user_ask") or {}).get("content"),
                    (state_payload.get("last_assistant_commitment") or {}).get("content"),
                    json.dumps(state_payload.get("open_loops") or [], ensure_ascii=False),
                    json.dumps(state_payload.get("pending_actions") or [], ensure_ascii=False),
                    json.dumps(state_payload.get("unresolved_state") or [], ensure_ascii=False),
                    (state_payload.get("latest_checkpoint") or {}).get("id"),
                    json.dumps({"summary_text": state_payload.get("summary_text"), "active_branch": state_payload.get("active_branch"), "latest_user_intent": state_payload.get("latest_user_intent")}, ensure_ascii=False),
                    store.SCHEMA_VERSION,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    store.submit_write(_write, timeout=30.0)
    emit_event(LOGFILE, "brain_conversation_state_upserted", status="ok", scope_type=scope_type)
    return get_state(conversation_id=conversation_id, session_id=session_id, thread_id=thread_id)


def get_state(
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    scope_type, scope_id = _scope_parts(
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
    )
    if not scope_type or not scope_id:
        return None

    conn = store.connect()
    try:
        row = conn.execute(
            "SELECT * FROM conversation_state WHERE scope_type = ? AND scope_id = ?",
            (scope_type, scope_id),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None

    def _load(value: Any) -> Any:
        try:
            return json.loads(value or "[]")
        except Exception:
            return []

    try:
        meta = json.loads(row["metadata_json"] or "{}")
    except Exception:
        meta = {}

    return {
        "id": int(row["id"]),
        "scope_type": row["scope_type"],
        "scope_id": row["scope_id"],
        "conversation_id": row["conversation_id"],
        "session_id": row["session_id"],
        "thread_id": row["thread_id"],
        "latest_user_turn_id": row["latest_user_turn_id"],
        "latest_assistant_turn_id": row["latest_assistant_turn_id"],
        "latest_user_ask": row["latest_user_ask"],
        "last_assistant_commitment": row["last_assistant_commitment"],
        "open_loops": _load(row["open_loops_json"]),
        "pending_actions": _load(row["pending_actions_json"]),
        "unresolved_state": _load(row["unresolved_state_json"]),
        "latest_checkpoint_id": row["latest_checkpoint_id"],
        "metadata": meta,
        "updated_at": row["updated_at"],
    }


def create_checkpoint(
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    upto_turn_id: Optional[int] = None,
    turns_limit: int = 24,
    checkpoint_kind: str = "manual",
) -> Optional[Dict[str, Any]]:
    turns = get_recent_turns(
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
        limit=turns_limit,
        upto_turn_id=upto_turn_id,
    )
    if not turns:
        return None
    unresolved_items = list_relevant_unresolved_state(
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
        limit=10,
    )
    latest_existing_checkpoint = get_latest_checkpoint(
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
    )
    derived = infer_hydration_payload(
        turns,
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
        unresolved_items=unresolved_items,
        latest_checkpoint=latest_existing_checkpoint,
    )
    summary_parts = [
        f"{len(turns)} recent turns captured",
    ]
    latest_user = derived.get("latest_user_ask") or {}
    if latest_user.get("effective_content") or latest_user.get("content"):
        summary_parts.append(f"user asked: {latest_user.get('effective_content') or latest_user.get('content')}")
    commitment = derived.get("last_assistant_commitment") or {}
    if commitment.get("content"):
        summary_parts.append(f"assistant committed: {commitment['content']}")
    if derived.get("open_loops"):
        summary_parts.append(f"open loops: {len(derived['open_loops'])}")
    summary_text = " | ".join(summary_parts)
    turn_start_id = int(turns[0]["id"])
    turn_end_id = int(turns[-1]["id"])
    parent_checkpoint = None
    if latest_existing_checkpoint and int(latest_existing_checkpoint.get("turn_end_id") or 0) < turn_end_id:
        parent_checkpoint = latest_existing_checkpoint
    parent_checkpoint_id = (int(parent_checkpoint.get("id") or 0) or None) if parent_checkpoint else None
    root_checkpoint_id = (
        int(parent_checkpoint.get("root_checkpoint_id") or parent_checkpoint_id or 0) or None
        if parent_checkpoint
        else None
    )
    checkpoint_depth = int(parent_checkpoint.get("depth") or 0) + 1 if parent_checkpoint else 0
    supporting_turn_ids = [int(turn.get("id") or 0) for turn in turns]

    def _write() -> int:
        conn = store.connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO conversation_checkpoints (
                    conversation_id, session_id, thread_id,
                    turn_start_id, turn_end_id, checkpoint_kind, summary,
                    latest_user_ask, last_assistant_commitment,
                    open_loops_json, pending_actions_json,
                    parent_checkpoint_id, root_checkpoint_id, depth, metadata_json, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    session_id,
                    thread_id,
                    turn_start_id,
                    turn_end_id,
                    checkpoint_kind,
                    summary_text,
                    latest_user.get("effective_content") or latest_user.get("content"),
                    commitment.get("content"),
                    json.dumps(derived.get("open_loops") or [], ensure_ascii=False),
                    json.dumps(derived.get("pending_actions") or [], ensure_ascii=False),
                    parent_checkpoint_id,
                    root_checkpoint_id,
                    checkpoint_depth,
                    json.dumps(
                        {
                            "turn_count": len(turns),
                            "latest_turn_reference": turns[-1].get("reference"),
                            "latest_turn_timestamp": turns[-1].get("timestamp"),
                            "unresolved_count": len(unresolved_items),
                            "supporting_turn_ids": supporting_turn_ids,
                            "active_branch": derived.get("active_branch"),
                        },
                        ensure_ascii=False,
                    ),
                    store.SCHEMA_VERSION,
                ),
            )
            conn.commit()
            checkpoint_id = int(cur.lastrowid)
            if root_checkpoint_id is None:
                conn.execute(
                    "UPDATE conversation_checkpoints SET root_checkpoint_id = ? WHERE id = ? AND root_checkpoint_id IS NULL",
                    (checkpoint_id, checkpoint_id),
                )
                conn.commit()
            return checkpoint_id
        finally:
            conn.close()

    checkpoint_id = int(store.submit_write(_write, timeout=30.0))
    emit_event(LOGFILE, "brain_conversation_checkpoint_created", status="ok", checkpoint_id=checkpoint_id, checkpoint_kind=checkpoint_kind)
    payload = get_checkpoint_by_id(checkpoint_id)
    try:
        refresh_state(conversation_id=conversation_id, session_id=session_id, thread_id=thread_id)
    except Exception as exc:
        emit_event(
            LOGFILE,
            "brain_conversation_checkpoint_post_write_maintenance_failed",
            status="warn",
            error=str(exc),
            checkpoint_id=checkpoint_id,
        )
    return payload


def _row_to_checkpoint(row) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    try:
        loops = json.loads(row["open_loops_json"] or "[]")
    except Exception:
        loops = []
    try:
        pending = json.loads(row["pending_actions_json"] or "[]")
    except Exception:
        pending = []
    try:
        meta = json.loads(row["metadata_json"] or "{}")
    except Exception:
        meta = {}
    return {
        "id": int(row["id"]),
        "reference": f"conversation_checkpoints:{row['id']}",
        "timestamp": row["timestamp"],
        "conversation_id": row["conversation_id"],
        "session_id": row["session_id"],
        "thread_id": row["thread_id"],
        "turn_start_id": row["turn_start_id"],
        "turn_end_id": row["turn_end_id"],
        "checkpoint_kind": row["checkpoint_kind"],
        "summary": row["summary"],
        "latest_user_ask": row["latest_user_ask"],
        "last_assistant_commitment": row["last_assistant_commitment"],
        "parent_checkpoint_id": row["parent_checkpoint_id"] if "parent_checkpoint_id" in row.keys() else None,
        "root_checkpoint_id": row["root_checkpoint_id"] if "root_checkpoint_id" in row.keys() else None,
        "depth": row["depth"] if "depth" in row.keys() else 0,
        "open_loops": loops,
        "pending_actions": pending,
        "metadata": meta,
    }


def get_checkpoint_by_id(checkpoint_id: int) -> Optional[Dict[str, Any]]:
    conn = store.connect()
    try:
        row = conn.execute("SELECT * FROM conversation_checkpoints WHERE id = ?", (int(checkpoint_id),)).fetchone()
    finally:
        conn.close()
    return _row_to_checkpoint(row)


def list_checkpoints(
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    where, params = _scope_where(
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
    )
    conn = store.connect()
    try:
        rows = conn.execute(
            f"SELECT * FROM conversation_checkpoints{where} ORDER BY id DESC LIMIT ?",
            (*params, min(max(limit, 1), 200)),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_checkpoint(row) for row in rows if row]


def get_checkpoint_lineage(checkpoint_id: Optional[int]) -> List[Dict[str, Any]]:
    if not checkpoint_id:
        return []
    lineage: List[Dict[str, Any]] = []
    current = get_checkpoint_by_id(int(checkpoint_id))
    seen: set[int] = set()
    while current:
        current_id = int(current.get("id") or 0)
        if not current_id or current_id in seen:
            break
        seen.add(current_id)
        lineage.append(current)
        parent_id = current.get("parent_checkpoint_id")
        current = get_checkpoint_by_id(int(parent_id)) if parent_id else None
    return list(reversed(lineage))


def get_checkpoint_children(checkpoint_id: int, *, limit: int = 20) -> List[Dict[str, Any]]:
    conn = store.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM conversation_checkpoints WHERE parent_checkpoint_id = ? ORDER BY id ASC LIMIT ?",
            (int(checkpoint_id), min(max(limit, 1), 100)),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_checkpoint(row) for row in rows if row]


def expand_checkpoint(checkpoint_id: int, *, radius_turns: int = 0, turns_limit: int = 200) -> Optional[Dict[str, Any]]:
    checkpoint = get_checkpoint_by_id(checkpoint_id)
    if not checkpoint:
        return None
    start_id = max(1, int(checkpoint.get("turn_start_id") or 0) - max(0, radius_turns))
    end_id = int(checkpoint.get("turn_end_id") or 0) + max(0, radius_turns)
    scope = _checkpoint_scope_filter(checkpoint)
    turns = _get_turns_between_ids(start_id, end_id, limit=turns_limit, **scope)
    lineage = get_checkpoint_lineage(checkpoint_id)
    children = get_checkpoint_children(checkpoint_id, limit=20)
    active_branch = _active_branch_payload(turns)
    checkpoint_candidates: List[Dict[str, Any]] = []
    seen_checkpoint_ids: set[int] = set()
    for candidate in [*lineage, checkpoint, *children]:
        candidate_id = int(candidate.get("id") or 0)
        if not candidate_id or candidate_id in seen_checkpoint_ids:
            continue
        seen_checkpoint_ids.add(candidate_id)
        checkpoint_candidates.append(candidate)
    return {
        "checkpoint": checkpoint,
        "lineage": lineage,
        "children": children,
        "supporting_turns": turns,
        "active_branch": active_branch,
        "salience_ranked_turns": _ranked_turn_expansion(turns, active_branch, limit=min(turns_limit, 12)),
        "salience_ranked_checkpoints": _ranked_checkpoint_expansion(checkpoint_candidates, active_branch, limit=12),
    }


def expand_turn(turn_id: int, *, radius_turns: int = 4, turns_limit: int = 80) -> Optional[Dict[str, Any]]:
    turn = _get_turn_by_id(turn_id)
    if not turn:
        return None
    scope = {
        "conversation_id": turn.get("conversation_id"),
        "session_id": turn.get("session_id"),
        "thread_id": turn.get("thread_id"),
    }
    center_turn_id = int(turn.get("id") or 0)
    start_id = max(1, center_turn_id - max(0, radius_turns))
    end_id = center_turn_id + max(0, radius_turns)
    turns = _get_turns_between_ids(start_id, end_id, limit=turns_limit, **scope)
    active_branch = _active_branch_payload(turns)
    checkpoint_candidates: List[Dict[str, Any]] = []
    for checkpoint in list_checkpoints(limit=20, **scope):
        start = int(checkpoint.get("turn_start_id") or 0)
        end = int(checkpoint.get("turn_end_id") or 0)
        if start <= center_turn_id <= end:
            checkpoint_candidates.append(checkpoint)
    return {
        "turn": turn,
        "reply_chain": _reply_chain_for_turn(turn, turns, limit=8),
        "supporting_turns": turns,
        "active_branch": active_branch,
        "related_checkpoints": checkpoint_candidates,
        "salience_ranked_turns": _ranked_turn_expansion(turns, active_branch, limit=min(turns_limit, 12)),
        "salience_ranked_checkpoints": _ranked_checkpoint_expansion(checkpoint_candidates, active_branch, limit=12),
    }


def get_latest_checkpoint(
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    where, params = _scope_where(
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
    )
    conn = store.connect()
    try:
        row = conn.execute(
            f"SELECT * FROM conversation_checkpoints{where} ORDER BY id DESC LIMIT 1",
            tuple(params),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_checkpoint(row)


def refresh_state(
    *,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    tolerate_write_failure: bool = False,
) -> Optional[Dict[str, Any]]:
    turns = get_recent_turns(
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
        limit=_MAX_STATE_TURNS,
    )
    unresolved_items = list_relevant_unresolved_state(
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
        limit=10,
    )
    latest_checkpoint = get_latest_checkpoint(
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
    )
    payload = infer_hydration_payload(
        turns,
        conversation_id=conversation_id,
        session_id=session_id,
        thread_id=thread_id,
        unresolved_items=unresolved_items,
        latest_checkpoint=latest_checkpoint,
    )
    try:
        return _upsert_state(
            conversation_id=conversation_id,
            session_id=session_id,
            thread_id=thread_id,
            state_payload=payload,
        )
    except Exception as exc:
        if not tolerate_write_failure:
            raise
        emit_event(
            LOGFILE,
            "brain_conversation_state_refresh_degraded",
            status="warn",
            error=str(exc),
            conversation_id=conversation_id,
            session_id=session_id,
            thread_id=thread_id,
        )
        existing = get_state(
            conversation_id=conversation_id,
            session_id=session_id,
            thread_id=thread_id,
        )
        if existing:
            metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
            existing["metadata"] = {**metadata, "state_status": "stale_persisted"}
            return existing
        return _state_from_payload(
            payload,
            conversation_id=conversation_id,
            session_id=session_id,
            thread_id=thread_id,
        )
