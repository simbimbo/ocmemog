from __future__ import annotations

import json
import re
import threading
from queue import Queue
from typing import Any, Callable, Dict, List, Optional

from brain.runtime import config, inference, state_store
from brain.runtime.instrumentation import emit_event
from brain.runtime.memory import api, integrity, memory_consolidation, memory_links, store, unresolved_state, vector_index

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"
_WRITABLE_MEMORY_TABLES = {"knowledge", "reflections", "directives", "tasks", "runbooks", "lessons"}
_SUMMARY_PREFIX_RE = re.compile(r"^(?:insight|recommendation|lesson)\s*:\s*", re.IGNORECASE)


def _run_with_timeout(name: str, fn: Callable[[], Any], timeout_s: float, default: Any) -> Any:
    emit_event(LOGFILE, f"brain_ponder_{name}_start", status="ok")
    result_queue: Queue[tuple[str, Any]] = Queue(maxsize=1)

    def _target() -> None:
        try:
            result_queue.put(("ok", fn()))
        except Exception as exc:  # pragma: no cover
            result_queue.put(("error", exc))

    worker = threading.Thread(target=_target, name=f"ocmemog-ponder-{name}", daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        emit_event(LOGFILE, f"brain_ponder_{name}_complete", status="timeout")
        return default
    if result_queue.empty():
        emit_event(LOGFILE, f"brain_ponder_{name}_complete", status="error", error="missing_result")
        return default
    status, payload = result_queue.get_nowait()
    if status == "error":
        emit_event(LOGFILE, f"brain_ponder_{name}_complete", status="error", error=str(payload))
        return default
    emit_event(LOGFILE, f"brain_ponder_{name}_complete", status="ok")
    return payload


def _infer_with_timeout(prompt: str, timeout_s: float = 20.0) -> Dict[str, str]:
    return _run_with_timeout(
        "infer",
        lambda: inference.infer(prompt, provider_name=config.OCMEMOG_PONDER_MODEL),
        timeout_s,
        {"status": "timeout", "output": ""},
    )


def _load_recent(table: str, limit: int) -> List[Dict[str, object]]:
    if table not in _WRITABLE_MEMORY_TABLES:
        return []
    conn = store.connect(ensure_schema=False)
    try:
        rows = conn.execute(
            f"SELECT id, content, confidence, timestamp, source, metadata_json FROM {table} ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    items: List[Dict[str, object]] = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except Exception:
            metadata = {}
        items.append(
            {
                "reference": f"{table}:{row['id']}",
                "content": str(row["content"] or ""),
                "confidence": float(row["confidence"] or 0.0),
                "timestamp": row["timestamp"],
                "source": row["source"],
                "metadata": metadata,
                "candidate_kind": "memory",
                "memory_type": table,
            }
        )
    return items


def _load_continuity_candidates(limit: int) -> List[Dict[str, object]]:
    conn = store.connect(ensure_schema=False)
    items: List[Dict[str, object]] = []
    try:
        checkpoint_rows = conn.execute(
            """
            SELECT id, session_id, thread_id, conversation_id, summary, latest_user_ask,
                   last_assistant_commitment, metadata_json, timestamp
            FROM conversation_checkpoints
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in checkpoint_rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except Exception:
                metadata = {}
            content_parts = [str(row["summary"] or "").strip()]
            latest_user_ask = str(row["latest_user_ask"] or "").strip()
            if latest_user_ask:
                content_parts.append(f"User ask: {latest_user_ask}")
            last_commitment = str(row["last_assistant_commitment"] or "").strip()
            if last_commitment:
                content_parts.append(f"Assistant commitment: {last_commitment}")
            items.append(
                {
                    "reference": f"conversation_checkpoints:{row['id']}",
                    "content": " | ".join(part for part in content_parts if part),
                    "timestamp": row["timestamp"],
                    "source": "continuity",
                    "metadata": {
                        **metadata,
                        "conversation_id": row["conversation_id"],
                        "session_id": row["session_id"],
                        "thread_id": row["thread_id"],
                    },
                    "candidate_kind": "checkpoint",
                    "memory_type": "runbooks",
                }
            )

        state_rows = conn.execute(
            """
            SELECT id, scope_type, scope_id, latest_user_ask, last_assistant_commitment,
                   open_loops_json, pending_actions_json, unresolved_state_json, metadata_json, updated_at
            FROM conversation_state
            ORDER BY updated_at DESC, id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in state_rows:
            try:
                open_loops = json.loads(row["open_loops_json"] or "[]")
            except Exception:
                open_loops = []
            try:
                pending_actions = json.loads(row["pending_actions_json"] or "[]")
            except Exception:
                pending_actions = []
            try:
                unresolved_items = json.loads(row["unresolved_state_json"] or "[]")
            except Exception:
                unresolved_items = []
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except Exception:
                metadata = {}
            content_parts = [f"Continuity scope {row['scope_type']}:{row['scope_id']}"]
            latest_user_ask = str(row["latest_user_ask"] or "").strip()
            if latest_user_ask:
                content_parts.append(f"Latest user ask: {latest_user_ask}")
            last_commitment = str(row["last_assistant_commitment"] or "").strip()
            if last_commitment:
                content_parts.append(f"Assistant commitment: {last_commitment}")
            for label, payload in (("Open loop", open_loops), ("Pending action", pending_actions), ("Unresolved", unresolved_items)):
                for item in payload[:2]:
                    summary = str((item or {}).get("summary") or "").strip()
                    if summary:
                        content_parts.append(f"{label}: {summary}")
            items.append(
                {
                    "reference": f"conversation_state:{row['id']}",
                    "content": " | ".join(part for part in content_parts if part),
                    "timestamp": row["updated_at"],
                    "source": "continuity",
                    "metadata": metadata,
                    "candidate_kind": "continuity_state",
                    "memory_type": "runbooks",
                }
            )

        turn_rows = conn.execute(
            """
            SELECT id, role, content, session_id, thread_id, conversation_id, message_id, metadata_json, timestamp
            FROM conversation_turns
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in turn_rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except Exception:
                metadata = {}
            items.append(
                {
                    "reference": f"conversation_turns:{row['id']}",
                    "content": f"{row['role']}: {str(row['content'] or '').strip()}",
                    "timestamp": row["timestamp"],
                    "source": "continuity",
                    "metadata": {
                        **metadata,
                        "conversation_id": row["conversation_id"],
                        "session_id": row["session_id"],
                        "thread_id": row["thread_id"],
                        "message_id": row["message_id"],
                    },
                    "candidate_kind": "turn",
                    "memory_type": "reflections",
                }
            )
    except Exception as exc:
        emit_event(LOGFILE, "brain_ponder_continuity_candidates_failed", status="error", error=str(exc))
    finally:
        conn.close()
    return items[:limit]


def _dedupe_candidates(items: List[Dict[str, object]], limit: int) -> List[Dict[str, object]]:
    deduped: List[Dict[str, object]] = []
    seen: set[str] = set()
    for item in items:
        reference = str(item.get("reference") or "")
        content = str(item.get("content") or "").strip()
        key = reference or content.lower()
        if not key or key in seen or not content:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def _heuristic_summary(text: str, limit: int = 220) -> str:
    collapsed = re.sub(r"\s+", " ", text or "").strip()
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[: limit - 1].rstrip()}…"


def _heuristic_ponder(record: Dict[str, object]) -> Dict[str, str]:
    text = str(record.get("content") or "").strip()
    reference = str(record.get("reference") or "")
    kind = str(record.get("candidate_kind") or "memory")
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    summary = _heuristic_summary(text)
    if kind == "checkpoint":
        return {
            "insight": f"Checkpoint captured active continuity: {summary}",
            "recommendation": "Promote the checkpoint summary into durable reflections and keep linked open loops hydrated at answer time.",
        }
    if kind == "continuity_state":
        return {
            "insight": f"Conversation continuity still carries unresolved context: {summary}",
            "recommendation": "Hydrate this scope before answering so pending actions and open loops stay visible after restarts.",
        }
    if kind == "turn":
        role = str(metadata.get("role") or "conversation")
        return {
            "insight": f"Recent {role} turn may shape near-term continuity: {summary}",
            "recommendation": "Retain the turn in short-horizon context and checkpoint if it changes the active branch or next action.",
        }
    return {
        "insight": f"Recent memory worth reinforcing: {summary}",
        "recommendation": "Link the reflection back to its source memory so future retrieval can hydrate it with provenance.",
    }


def _parse_structured_output(output: str) -> Dict[str, str]:
    insight = ""
    recommendation = ""
    for line in output.splitlines():
        if line.lower().startswith("insight:"):
            insight = line.split(":", 1)[-1].strip()
        elif line.lower().startswith("recommendation:"):
            recommendation = line.split(":", 1)[-1].strip()
    cleaned = [
        _SUMMARY_PREFIX_RE.sub("", line).strip()
        for line in output.splitlines()
        if _SUMMARY_PREFIX_RE.sub("", line).strip()
    ]
    if not insight and cleaned:
        insight = cleaned[0]
    if not recommendation and len(cleaned) > 1:
        recommendation = cleaned[1]
    return {"insight": insight[:280], "recommendation": recommendation[:280]}


def _ponder_with_model(record: Dict[str, object]) -> Dict[str, str]:
    text = str(record.get("content") or "").strip()
    if not text:
        return {"insight": "", "recommendation": ""}
    prompt = (
        "You are the memory pondering engine.\n"
        "Given this memory/context item, return: (1) a concise insight, (2) a concrete recommendation.\n"
        "Keep both actionable and under 220 characters each.\n\n"
        f"Reference: {record.get('reference')}\n"
        f"Kind: {record.get('candidate_kind') or 'memory'}\n"
        f"Memory: {text}\n\n"
        "Format:\nInsight: ...\nRecommendation: ..."
    )
    result = _infer_with_timeout(prompt)
    output = str(result.get("output") or "").strip()
    parsed = _parse_structured_output(output)
    if parsed.get("insight") and parsed.get("recommendation"):
        return parsed
    heuristic = _heuristic_ponder(record)
    return {
        "insight": parsed.get("insight") or heuristic["insight"],
        "recommendation": parsed.get("recommendation") or heuristic["recommendation"],
    }


def _extract_lesson(record: Dict[str, object]) -> str | None:
    text = str(record.get("content") or "").strip()
    if not text:
        return None
    prompt = (
        "Extract a single actionable lesson learned from this memory/context item.\n"
        "If there is no clear lesson, reply with NONE. Keep it under 220 characters.\n\n"
        f"Reference: {record.get('reference')}\n"
        f"Memory: {text}\n\n"
        "Lesson:"
    )
    result = _infer_with_timeout(prompt)
    output = str(result.get("output") or "").strip()
    if not output or output.upper().startswith("NONE"):
        return None
    output = _SUMMARY_PREFIX_RE.sub("", output).strip()
    return output[:240] if output else None


def _memory_exists(memory_type: str, content: str, metadata: Optional[Dict[str, object]] = None) -> Optional[int]:
    if memory_type not in _WRITABLE_MEMORY_TABLES:
        return None
    conn = store.connect(ensure_schema=False)
    try:
        rows = conn.execute(
            f"SELECT id, metadata_json FROM {memory_type} WHERE content = ? ORDER BY id DESC LIMIT 25",
            (content,),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    if not rows:
        return None
    wanted_ref = str((metadata or {}).get("source_reference") or "")
    for row in rows:
        if not wanted_ref:
            return int(row["id"])
        try:
            row_meta = json.loads(row["metadata_json"] or "{}")
        except Exception:
            row_meta = {}
        if str(row_meta.get("source_reference") or "") == wanted_ref:
            return int(row["id"])
    return None


def _link_once(source_reference: str, link_type: str, target_reference: str) -> None:
    if not source_reference or not target_reference:
        return
    existing = memory_links.get_memory_links(source_reference)
    if any(item.get("link_type") == link_type and item.get("target_reference") == target_reference for item in existing):
        return
    memory_links.add_memory_link(source_reference, link_type, target_reference)


def _store_reflection(summary: str, *, source_reference: str, recommendation: str = "", metadata: Optional[Dict[str, object]] = None) -> str:
    content = summary.strip()
    if recommendation.strip():
        content = f"{content}\nRecommendation: {recommendation.strip()}"
    content = content.strip()
    reflection_metadata = {**(metadata or {}), "source_reference": source_reference, "kind": "ponder_reflection"}
    existing_id = _memory_exists("reflections", content, reflection_metadata)
    if existing_id:
        return f"reflections:{existing_id}"
    reflection_id = api.store_memory("reflections", content, source="ponder", metadata=reflection_metadata)
    reflection_ref = f"reflections:{reflection_id}"
    _link_once(reflection_ref, "derived_from", source_reference)
    return reflection_ref


def _store_lesson_once(lesson: str, *, source_reference: str) -> Optional[str]:
    normalized = lesson.strip()
    if not normalized:
        return None
    metadata = {"reference": source_reference, "source_reference": source_reference, "kind": "ponder_lesson"}
    existing_id = _memory_exists("lessons", normalized, metadata)
    if existing_id:
        return f"lessons:{existing_id}"
    lesson_id = api.store_memory("lessons", normalized, source="ponder", metadata=metadata)
    lesson_ref = f"lessons:{lesson_id}"
    _link_once(lesson_ref, "derived_from", source_reference)
    return lesson_ref


def _candidate_memories(max_items: int) -> List[Dict[str, object]]:
    base_candidates: List[Dict[str, object]] = []
    for table in ("reflections", "knowledge", "tasks", "runbooks"):
        base_candidates.extend(_load_recent(table, max_items))
    base_candidates.extend(_load_continuity_candidates(max_items))
    return _dedupe_candidates(base_candidates, max_items)


def run_ponder_cycle(max_items: int = 5) -> Dict[str, object]:
    emit_event(LOGFILE, "brain_ponder_cycle_start", status="ok")

    unresolved = _run_with_timeout(
        "unresolved",
        lambda: unresolved_state.list_unresolved_state(limit=max_items),
        5.0,
        [],
    )
    candidates = _candidate_memories(max_items)
    consolidation = _run_with_timeout(
        "consolidation",
        lambda: memory_consolidation.consolidate_memories(candidates, max_clusters=max_items),
        15.0,
        {"consolidated": [], "reinforcement": []},
    )

    insights: List[Dict[str, object]] = []
    for item in unresolved[:max_items]:
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        source_reference = str(item.get("reference") or "") or str(item.get("target_reference") or "")
        reflection_ref = _store_reflection(
            f"Unresolved state remains active: {summary}",
            source_reference=source_reference or "unresolved_state",
            recommendation="Resolve or checkpoint this item so it stays visible during future hydration.",
            metadata={"state_type": item.get("state_type"), "kind": "unresolved"},
        )
        insights.append(
            {
                "type": "unresolved",
                "summary": summary,
                "reference": source_reference,
                "reflection_reference": reflection_ref,
            }
        )
        emit_event(LOGFILE, "brain_ponder_insight_generated", status="ok", kind="unresolved")

    if str(config.OCMEMOG_PONDER_ENABLED).lower() in {"1", "true", "yes"}:
        for item in candidates:
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            model_result = _ponder_with_model(item)
            insight = str(model_result.get("insight") or "").strip()
            recommendation = str(model_result.get("recommendation") or "").strip()
            if not insight:
                continue
            reference = str(item.get("reference") or "")
            reflection_ref = _store_reflection(
                insight,
                source_reference=reference or "ponder",
                recommendation=recommendation,
                metadata={
                    "candidate_kind": item.get("candidate_kind"),
                    "memory_type": item.get("memory_type"),
                },
            )
            insights.append(
                {
                    "type": str(item.get("candidate_kind") or "memory"),
                    "reference": reference,
                    "summary": insight,
                    "recommendation": recommendation,
                    "reflection_reference": reflection_ref,
                }
            )
            emit_event(LOGFILE, "brain_ponder_insight_generated", status="ok", kind=str(item.get("candidate_kind") or "memory"))

    lessons: List[Dict[str, object]] = []
    if str(config.OCMEMOG_LESSON_MINING_ENABLED).lower() in {"1", "true", "yes"}:
        for item in candidates:
            reference = str(item.get("reference") or "")
            if not reference:
                continue
            if not (reference.startswith("reflections:") or reference.startswith("conversation_checkpoints:")):
                continue
            lesson = _extract_lesson(item)
            if not lesson:
                continue
            lesson_ref = _store_lesson_once(lesson, source_reference=reference)
            lessons.append({"reference": reference, "lesson": lesson, "lesson_reference": lesson_ref})
            emit_event(LOGFILE, "brain_ponder_lesson_generated", status="ok")

    links: List[Dict[str, object]] = []
    for cluster in consolidation.get("consolidated", []):
        summary = str(cluster.get("summary") or "").strip()
        if not summary:
            continue
        reflection_ref = _store_reflection(
            f"Consolidated pattern: {summary}",
            source_reference=str(cluster.get("references", ["ponder"])[0]),
            recommendation=f"Review grouped references together ({int(cluster.get('count') or 0)} items).",
            metadata={"kind": "cluster", "cluster_kind": cluster.get("memory_type")},
        )
        for target_reference in cluster.get("references", []) or []:
            if isinstance(target_reference, str) and target_reference:
                _link_once(reflection_ref, "conceptual", target_reference)
        links.append(
            {
                "type": "cluster",
                "summary": summary,
                "count": int(cluster.get("count") or 0),
                "references": cluster.get("references") or [],
                "reflection_reference": reflection_ref,
            }
        )

    maintenance = _run_with_timeout(
        "integrity",
        integrity.run_integrity_check,
        10.0,
        {"issues": []},
    )
    if "vector_orphan" in set(maintenance.get("repairable_issues") or []):
        maintenance["repair"] = _run_with_timeout(
            "integrity_repair",
            integrity.repair_integrity,
            10.0,
            {"ok": False, "repaired": []},
        )
        maintenance = _run_with_timeout(
            "integrity_post_repair",
            integrity.run_integrity_check,
            10.0,
            maintenance,
        )
    if any(item.startswith("vector_missing") or item.startswith("vector_orphan") for item in maintenance.get("issues", [])):
        rebuild_count = _run_with_timeout(
            "vector_rebuild",
            vector_index.rebuild_vector_index,
            30.0,
            0,
        )
        maintenance["vector_rebuild"] = rebuild_count

    emit_event(
        LOGFILE,
        "brain_ponder_cycle_complete",
        status="ok",
        candidates=len(candidates),
        insights=len(insights),
        lessons=len(lessons),
        links=len(links),
    )
    return {
        "unresolved": unresolved,
        "candidates": candidates,
        "insights": insights,
        "lessons": lessons,
        "links": links,
        "maintenance": maintenance,
        "consolidation": consolidation,
    }
