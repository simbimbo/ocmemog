from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from datetime import datetime, timedelta

from brain.runtime import state_store
from brain.runtime.memory import api, conversation_state, distill, health, memory_links, pondering_engine, provenance, reinforcement, retrieval, store
from ocmemog.sidecar.compat import flatten_results, probe_runtime
from ocmemog.sidecar.transcript_watcher import watch_forever

DEFAULT_CATEGORIES = ("knowledge", "reflections", "directives", "tasks", "runbooks", "lessons")

app = FastAPI(title="ocmemog sidecar", version="0.0.1")

API_TOKEN = os.environ.get("OCMEMOG_API_TOKEN")

QUEUE_LOCK = threading.Lock()
QUEUE_PROCESS_LOCK = threading.Lock()
QUEUE_STATS = {
    "last_run": None,
    "processed": 0,
    "errors": 0,
    "last_error": None,
    "last_batch": 0,
}


def _queue_stats_path() -> Path:
    path = state_store.data_dir() / "queue_stats.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_queue_stats() -> None:
    path = _queue_stats_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(data, dict):
        return
    for key in list(QUEUE_STATS.keys()):
        if key in data:
            QUEUE_STATS[key] = data[key]


def _save_queue_stats() -> None:
    path = _queue_stats_path()
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(QUEUE_STATS, indent=2, sort_keys=True), encoding='utf-8')
    tmp.replace(path)


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    if API_TOKEN:
        header = request.headers.get("x-ocmemog-token") or request.headers.get("authorization", "")
        token = header.replace("Bearer ", "") if header else ""
        if token != API_TOKEN:
            return JSONResponse(status_code=401, content={"ok": False, "error": "unauthorized"})
    return await call_next(request)


@app.on_event("startup")
def _start_transcript_watcher() -> None:
    enabled = os.environ.get("OCMEMOG_TRANSCRIPT_WATCHER", "").lower() in {"1", "true", "yes"}
    if not enabled:
        return
    thread = threading.Thread(target=watch_forever, daemon=True)
    thread.start()


def _queue_path() -> Path:
    path = state_store.data_dir() / "ingest_queue.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("")
    return path


def _queue_depth() -> int:
    path = _queue_path()
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return sum(1 for line in handle if line.strip())
    except Exception:
        return 0


def _write_queue_lines(lines: List[str]) -> None:
    path = _queue_path()
    temp = path.with_suffix(".jsonl.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for line in lines:
            if line.strip():
                handle.write(line.rstrip("\n") + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)


def _read_queue_lines() -> List[str]:
    path = _queue_path()
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return [line.rstrip("\n") for line in handle if line.strip()]
    except Exception:
        return []


def _peek_queue_batch(limit: Optional[int] = None) -> tuple[List[tuple[int, Dict[str, Any]]], int]:
    lines = _read_queue_lines()
    if not lines:
        return [], 0
    batch: List[tuple[int, Dict[str, Any]]] = []
    consumed_lines = 0
    max_items = limit if limit is not None and limit > 0 else len(lines)
    for line in lines:
        if len(batch) >= max_items:
            break
        consumed_lines += 1
        try:
            batch.append((consumed_lines, json.loads(line)))
        except Exception:
            QUEUE_STATS["errors"] += 1
            QUEUE_STATS["last_error"] = "invalid_queue_payload"
    return batch, consumed_lines


def _ack_queue_batch(consumed_lines: int) -> None:
    if consumed_lines <= 0:
        return
    with QUEUE_LOCK:
        lines = _read_queue_lines()
        _write_queue_lines(lines[consumed_lines:])


def _enqueue_payload(payload: Dict[str, Any]) -> int:
    path = _queue_path()
    line = json.dumps(payload, ensure_ascii=False)
    with QUEUE_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return _queue_depth()



def _process_queue(limit: Optional[int] = None) -> Dict[str, Any]:
    processed = 0
    errors = 0
    last_error = None
    batch_limit = limit if limit is not None and limit > 0 else None

    with QUEUE_PROCESS_LOCK:
        while True:
            remaining_budget = None
            if batch_limit is not None:
                remaining_budget = batch_limit - processed
                if remaining_budget <= 0:
                    break
            batch, consumed_lines = _peek_queue_batch(remaining_budget)
            if consumed_lines <= 0:
                break

            batch_processed = 0
            acknowledged = 0
            for line_no, payload in batch:
                try:
                    req = IngestRequest(**payload)
                    _ingest_request(req)
                    processed += 1
                    batch_processed += 1
                    acknowledged = line_no
                except Exception as exc:
                    errors += 1
                    last_error = str(exc)
                    break

            if not errors:
                acknowledged = consumed_lines
            _ack_queue_batch(acknowledged)

            if errors:
                break
            if not batch:
                break

    QUEUE_STATS["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
    QUEUE_STATS["last_batch"] = processed
    QUEUE_STATS["processed"] += processed
    if errors:
        QUEUE_STATS["errors"] += errors
    if last_error:
        QUEUE_STATS["last_error"] = last_error
    _save_queue_stats()
    return {"processed": processed, "errors": errors, "last_error": last_error}



def _ingest_worker() -> None:
    enabled = os.environ.get("OCMEMOG_INGEST_ASYNC_WORKER", "true").lower() in {"1", "true", "yes"}
    if not enabled:
        return
    poll_seconds = float(os.environ.get("OCMEMOG_INGEST_ASYNC_POLL_SECONDS", "5"))
    batch_max = int(os.environ.get("OCMEMOG_INGEST_ASYNC_BATCH_MAX", "25"))

    while True:
        _process_queue(batch_max)
        time.sleep(poll_seconds)



def _drain_queue(limit: Optional[int] = None) -> Dict[str, Any]:
    return _process_queue(limit)


@app.on_event("startup")
def _start_ingest_worker() -> None:
    thread = threading.Thread(target=_ingest_worker, daemon=True)
    thread.start()


class SearchRequest(BaseModel):
    query: str = Field(default="")
    limit: int = Field(default=5, ge=1, le=50)
    categories: Optional[List[str]] = None


class GetRequest(BaseModel):
    reference: str


class ContextRequest(BaseModel):
    reference: str
    radius: int = Field(default=10, ge=0, le=200)


class RecentRequest(BaseModel):
    categories: Optional[List[str]] = Field(default=None, description="Filter by memory categories")
    limit: int = Field(default=12, ge=1, le=100, description="Maximum items per category")
    hours: Optional[int] = Field(default=36, ge=1, le=168, description="Lookback window in hours")


class PonderRequest(BaseModel):
    max_items: int = Field(default=5, ge=1, le=50)


class IngestRequest(BaseModel):
    content: str
    kind: str = Field(default="experience", description="experience or memory")
    memory_type: Optional[str] = Field(default=None, description="knowledge|reflections|directives|tasks|runbooks|lessons")
    source: Optional[str] = None
    task_id: Optional[str] = None
    conversation_id: Optional[str] = None
    session_id: Optional[str] = None
    thread_id: Optional[str] = None
    message_id: Optional[str] = None
    role: Optional[str] = None
    source_reference: Optional[str] = None
    source_references: Optional[List[str]] = None
    source_label: Optional[str] = None
    source_labels: Optional[List[str]] = None
    transcript_path: Optional[str] = None
    transcript_offset: Optional[int] = None
    transcript_end_offset: Optional[int] = None
    timestamp: Optional[str] = None


class ConversationTurnRequest(BaseModel):
    role: str
    content: str
    conversation_id: Optional[str] = None
    session_id: Optional[str] = None
    thread_id: Optional[str] = None
    message_id: Optional[str] = None
    source: Optional[str] = None
    transcript_path: Optional[str] = None
    transcript_offset: Optional[int] = None
    transcript_end_offset: Optional[int] = None
    timestamp: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ConversationHydrateRequest(BaseModel):
    conversation_id: Optional[str] = None
    session_id: Optional[str] = None
    thread_id: Optional[str] = None
    turns_limit: int = Field(default=12, ge=1, le=100)
    memory_limit: int = Field(default=8, ge=1, le=50)


class ConversationCheckpointRequest(BaseModel):
    conversation_id: Optional[str] = None
    session_id: Optional[str] = None
    thread_id: Optional[str] = None
    upto_turn_id: Optional[int] = None
    turns_limit: int = Field(default=24, ge=1, le=200)
    checkpoint_kind: str = Field(default="manual")


class ConversationCheckpointListRequest(BaseModel):
    conversation_id: Optional[str] = None
    session_id: Optional[str] = None
    thread_id: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=100)


class ConversationCheckpointExpandRequest(BaseModel):
    checkpoint_id: int = Field(ge=1)
    radius_turns: int = Field(default=0, ge=0, le=25)
    turns_limit: int = Field(default=100, ge=1, le=300)


class ConversationTurnExpandRequest(BaseModel):
    turn_id: int = Field(ge=1)
    radius_turns: int = Field(default=4, ge=0, le=25)
    turns_limit: int = Field(default=80, ge=1, le=300)


class DistillRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=100)


class ReinforceRequest(BaseModel):
    task_id: str
    outcome: str
    reward_score: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    memory_reference: str = Field(default="feedback")
    experience_type: str = Field(default="reinforcement")
    source_module: str = Field(default="sidecar")
    note: Optional[str] = None


def _fetch_recent(category: str, limit: int, since: Optional[str]) -> List[Dict[str, Any]]:
    conn = store.connect()
    items: List[Dict[str, Any]] = []
    try:
        if since:
            rows = conn.execute(
                f"SELECT id, content, metadata_json, timestamp FROM {category} WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT id, content, metadata_json, timestamp FROM {category} ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        for row in rows:
            try:
                meta = json.loads(row["metadata_json"] or "{}")
            except Exception:
                meta = {}
            items.append({
                "reference": f"{category}:{row['id']}",
                "timestamp": row["timestamp"],
                "content": row["content"],
                "metadata": meta,
            })
    finally:
        conn.close()
    return items


def _normalize_categories(categories: Optional[Iterable[str]]) -> List[str]:
    selected = [item for item in (categories or DEFAULT_CATEGORIES) if item in DEFAULT_CATEGORIES]
    return selected or list(DEFAULT_CATEGORIES)


def _runtime_payload() -> Dict[str, Any]:
    status = probe_runtime()
    return {
        "mode": status.mode,
        "missingDeps": status.missing_deps,
        "todo": status.todo,
        "warnings": status.warnings,
    }


def _fallback_search(query: str, limit: int, categories: List[str]) -> List[Dict[str, Any]]:
    conn = store.connect()
    try:
        results: List[Dict[str, Any]] = []
        for table in categories:
            rows = conn.execute(
                f"SELECT id, content, confidence FROM {table} WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
            for row in rows:
                results.append(
                    {
                        "bucket": table,
                        "reference": f"{table}:{row['id']}",
                        "table": table,
                        "id": str(row["id"]),
                        "content": str(row["content"] or ""),
                        "score": float(row["confidence"] or 0.0),
                        "links": [],
                    }
                )
        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:limit]
    finally:
        conn.close()


_ALLOWED_MEMORY_REFERENCE_TYPES = {
    "knowledge",
    "reflections",
    "directives",
    "tasks",
    "runbooks",
    "lessons",
    "conversation_turns",
    "conversation_checkpoints",
}


def _parse_reference(reference: str) -> tuple[str, str] | None:
    if not isinstance(reference, str) or ":" not in reference:
        return None
    prefix, identifier = reference.split(":", 1)
    prefix = prefix.strip()
    identifier = identifier.strip()
    if not prefix or not identifier:
        return None
    return prefix, identifier


def _get_row(reference: str) -> Optional[Dict[str, Any]]:
    parsed = _parse_reference(reference)
    if not parsed:
        return None
    prefix, identifier = parsed
    if prefix not in _ALLOWED_MEMORY_REFERENCE_TYPES:
        return None
    if prefix in {"knowledge", "reflections", "directives", "tasks", "runbooks", "lessons", "conversation_turns", "conversation_checkpoints"} and not identifier.isdigit():
        return None
    return provenance.hydrate_reference(reference, depth=2)


def _ingest_conversation_turn(request: ConversationTurnRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    try:
        turn_id = conversation_state.record_turn(
            role=request.role,
            content=request.content,
            conversation_id=request.conversation_id,
            session_id=request.session_id,
            thread_id=request.thread_id,
            message_id=request.message_id,
            transcript_path=request.transcript_path,
            transcript_offset=request.transcript_offset,
            transcript_end_offset=request.transcript_end_offset,
            source=request.source,
            timestamp=request.timestamp,
            metadata=request.metadata,
        )
    except ValueError as exc:
        if str(exc) == "internal_continuity_turn":
            return {
                "ok": True,
                "ignored": True,
                "reason": "internal_continuity_turn",
                **runtime,
            }
        raise
    return {
        "ok": True,
        "turn_id": turn_id,
        "reference": f"conversation_turns:{turn_id}",
        **runtime,
    }


def _parse_transcript_target(target: str) -> tuple[Path, Optional[int], Optional[int]] | None:
    if not target.startswith("transcript:"):
        return None
    raw = target[len("transcript:"):]
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    if "#L" in raw:
        path_str, anchor = raw.split("#L", 1)
        if "-L" in anchor:
            start_str, end_str = anchor.split("-L", 1)
            try:
                line_start = int(start_str)
            except Exception:
                line_start = None
            try:
                line_end = int(end_str)
            except Exception:
                line_end = None
        else:
            try:
                line_start = int(anchor)
            except Exception:
                line_start = None
    else:
        path_str = raw
    path = Path(path_str).expanduser()
    return (path, line_start, line_end)


def _allowed_transcript_roots() -> list[Path]:
    raw = os.environ.get("OCMEMOG_TRANSCRIPT_ROOTS")
    if raw:
        roots = [Path(item).expanduser().resolve() for item in raw.split(",") if item.strip()]
    else:
        roots = [Path.home() / ".openclaw" / "workspace" / "memory"]
    return roots


def _read_transcript_snippet(path: Path, line_start: Optional[int], line_end: Optional[int], radius: int) -> Dict[str, Any]:
    path = path.expanduser().resolve()
    allowed = _allowed_transcript_roots()
    if not any(path.is_relative_to(root) for root in allowed):
        return {"ok": False, "error": "transcript_path_not_allowed", "path": str(path)}
    if not path.exists() or not path.is_file():
        return {"ok": False, "error": "missing_transcript", "path": str(path)}

    anchor_start = line_start if line_start and line_start > 0 else None
    anchor_end = line_end if line_end and line_end > 0 else anchor_start
    if anchor_start is not None and anchor_end is not None and anchor_end < anchor_start:
        anchor_end = anchor_start

    if anchor_start is None:
        start_line = 1
        end_line = max(1, radius)
    else:
        start_line = max(1, anchor_start - radius)
        end_line = max(anchor_end or anchor_start, anchor_start) + radius

    snippet_lines = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for idx, line in enumerate(handle, start=1):
            if idx < start_line:
                continue
            if idx > end_line:
                break
            snippet_lines.append(line.rstrip("\n"))

    if not snippet_lines:
        return {"ok": False, "error": "empty_transcript", "path": str(path)}

    return {
        "ok": True,
        "path": str(path),
        "start_line": start_line,
        "end_line": start_line + len(snippet_lines) - 1,
        "anchor_start_line": anchor_start,
        "anchor_end_line": anchor_end,
        "snippet": "\n".join(snippet_lines),
    }


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    payload = _runtime_payload()
    payload["ok"] = True
    return payload


@app.post("/memory/search")
def memory_search(request: SearchRequest) -> dict[str, Any]:
    categories = _normalize_categories(request.categories)
    runtime = _runtime_payload()
    try:
        results = retrieval.retrieve_for_queries([request.query], limit=request.limit, categories=categories)
        flattened = flatten_results(results)
        used_fallback = False
    except Exception as exc:
        flattened = _fallback_search(request.query, request.limit, categories)
        used_fallback = True
        runtime["warnings"] = [*runtime["warnings"], f"search fallback enabled: {exc}"]

    return {
        "ok": True,
        "query": request.query,
        "limit": request.limit,
        "categories": categories,
        "results": flattened,
        "usedFallback": used_fallback,
        **runtime,
    }


@app.post("/memory/get")
def memory_get(request: GetRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    parsed = _parse_reference(request.reference)
    if not parsed:
        return {
            "ok": False,
            "error": "invalid_reference",
            "message": "Reference must be in the form type:id",
            "reference": request.reference,
            **runtime,
        }
    prefix, identifier = parsed
    if prefix not in _ALLOWED_MEMORY_REFERENCE_TYPES:
        return {
            "ok": False,
            "error": "unsupported_reference_type",
            "message": f"Unsupported memory reference type: {prefix}",
            "reference": request.reference,
            **runtime,
        }
    if prefix in {"knowledge", "reflections", "directives", "tasks", "runbooks", "lessons", "conversation_turns", "conversation_checkpoints"} and not identifier.isdigit():
        return {
            "ok": False,
            "error": "invalid_reference_id",
            "message": f"Reference id for {prefix} must be numeric",
            "reference": request.reference,
            **runtime,
        }
    row = provenance.hydrate_reference(request.reference, depth=2)
    if row is None:
        return {
            "ok": False,
            "error": "reference_not_found",
            "message": "Reference was well-formed but no matching memory was found",
            "reference": request.reference,
            **runtime,
        }

    return {
        "ok": True,
        "reference": request.reference,
        "memory": row,
        **runtime,
    }


@app.post("/memory/context")
def memory_context(request: ContextRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    links = memory_links.get_memory_links(request.reference)
    transcript = None
    for link in links:
        target = link.get("target_reference", "")
        parsed = _parse_transcript_target(target)
        if parsed:
            path, line_start, line_end = parsed
            transcript = _read_transcript_snippet(path, line_start, line_end, request.radius)
            break
    return {
        "ok": True,
        "reference": request.reference,
        "links": links,
        "transcript": transcript,
        "provenance": provenance.hydrate_reference(request.reference, depth=2),
        **runtime,
    }


@app.post("/memory/recent")
def memory_recent(request: RecentRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    categories = _normalize_categories(request.categories)
    since = None
    if request.hours:
        since = (datetime.utcnow() - timedelta(hours=request.hours)).strftime("%Y-%m-%d %H:%M:%S")
    results = {category: _fetch_recent(category, request.limit, since) for category in categories}
    return {
        "ok": True,
        "categories": categories,
        "since": since,
        "limit": request.limit,
        "results": results,
        **runtime,
    }


@app.post("/conversation/ingest_turn")
def conversation_ingest_turn(request: ConversationTurnRequest) -> dict[str, Any]:
    return _ingest_conversation_turn(request)


@app.post("/conversation/hydrate")
def conversation_hydrate(request: ConversationHydrateRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    turns = conversation_state.get_recent_turns(
        conversation_id=request.conversation_id,
        session_id=request.session_id,
        thread_id=request.thread_id,
        limit=request.turns_limit,
    )
    linked_memories = conversation_state.get_linked_memories(
        conversation_id=request.conversation_id,
        session_id=request.session_id,
        thread_id=request.thread_id,
        limit=request.memory_limit,
    )
    link_targets: List[Dict[str, Any]] = []
    if request.thread_id:
        link_targets.extend(memory_links.get_memory_links_for_thread(request.thread_id))
    if request.session_id:
        link_targets.extend(memory_links.get_memory_links_for_session(request.session_id))
    if request.conversation_id:
        link_targets.extend(memory_links.get_memory_links_for_conversation(request.conversation_id))
    latest_checkpoint = conversation_state.get_latest_checkpoint(
        conversation_id=request.conversation_id,
        session_id=request.session_id,
        thread_id=request.thread_id,
    )
    unresolved_items = conversation_state.list_relevant_unresolved_state(
        conversation_id=request.conversation_id,
        session_id=request.session_id,
        thread_id=request.thread_id,
        limit=10,
    )
    summary = conversation_state.infer_hydration_payload(
        turns,
        conversation_id=request.conversation_id,
        session_id=request.session_id,
        thread_id=request.thread_id,
        unresolved_items=unresolved_items,
        latest_checkpoint=latest_checkpoint,
        linked_memories=linked_memories,
    )
    state_payload = conversation_state.refresh_state(
        conversation_id=request.conversation_id,
        session_id=request.session_id,
        thread_id=request.thread_id,
        tolerate_write_failure=True,
    )
    state_meta = (state_payload or {}).get("metadata") if isinstance((state_payload or {}).get("metadata"), dict) else {}
    state_status = str(state_meta.get("state_status") or "")
    if state_status == "stale_persisted":
        runtime["warnings"] = [*runtime["warnings"], "hydrate returned persisted state while state refresh was delayed"]
    elif state_status == "derived_not_persisted":
        runtime["warnings"] = [*runtime["warnings"], "hydrate returned derived state while state refresh was delayed"]
    return {
        "ok": True,
        "conversation_id": request.conversation_id,
        "session_id": request.session_id,
        "thread_id": request.thread_id,
        "recent_turns": turns,
        "summary": summary,
        "turn_counts": conversation_state.get_turn_counts(
            conversation_id=request.conversation_id,
            session_id=request.session_id,
            thread_id=request.thread_id,
        ),
        "linked_memories": linked_memories,
        "linked_references": link_targets,
        "checkpoint_graph": summary.get("checkpoint_graph"),
        "active_branch": summary.get("active_branch"),
        "state": state_payload,
        **runtime,
    }


@app.post("/conversation/checkpoint")
def conversation_checkpoint(request: ConversationCheckpointRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    checkpoint = conversation_state.create_checkpoint(
        conversation_id=request.conversation_id,
        session_id=request.session_id,
        thread_id=request.thread_id,
        upto_turn_id=request.upto_turn_id,
        turns_limit=request.turns_limit,
        checkpoint_kind=request.checkpoint_kind,
    )
    if checkpoint is None:
        return {"ok": False, "error": "no_turns_available", **runtime}
    return {"ok": True, "checkpoint": checkpoint, **runtime}


@app.post("/conversation/checkpoints")
def conversation_checkpoints(request: ConversationCheckpointListRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    checkpoints = conversation_state.list_checkpoints(
        conversation_id=request.conversation_id,
        session_id=request.session_id,
        thread_id=request.thread_id,
        limit=request.limit,
    )
    return {
        "ok": True,
        "conversation_id": request.conversation_id,
        "session_id": request.session_id,
        "thread_id": request.thread_id,
        "checkpoints": checkpoints,
        **runtime,
    }


@app.post("/conversation/checkpoint_expand")
def conversation_checkpoint_expand(request: ConversationCheckpointExpandRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    expanded = conversation_state.expand_checkpoint(
        request.checkpoint_id,
        radius_turns=request.radius_turns,
        turns_limit=request.turns_limit,
    )
    if expanded is None:
        return {"ok": False, "error": "checkpoint_not_found", "checkpoint_id": request.checkpoint_id, **runtime}
    return {"ok": True, **expanded, **runtime}


@app.post("/conversation/turn_expand")
def conversation_turn_expand(request: ConversationTurnExpandRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    expanded = conversation_state.expand_turn(
        request.turn_id,
        radius_turns=request.radius_turns,
        turns_limit=request.turns_limit,
    )
    if expanded is None:
        return {"ok": False, "error": "turn_not_found", "turn_id": request.turn_id, **runtime}
    return {"ok": True, **expanded, **runtime}


@app.post("/memory/ponder")
def memory_ponder(request: PonderRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    results = pondering_engine.run_ponder_cycle(max_items=request.max_items)
    return {"ok": True, "results": results, **runtime}


@app.get("/memory/ponder/latest")
def memory_ponder_latest(limit: int = 5) -> dict[str, Any]:
    runtime = _runtime_payload()
    conn = store.connect()
    rows = conn.execute(
        "SELECT id, content, metadata_json, timestamp FROM reflections WHERE source='ponder' ORDER BY id DESC LIMIT ?",
        (min(max(limit, 1), 20),),
    ).fetchall()
    conn.close()
    items = []
    for row in rows:
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except Exception:
            meta = {}
        content = str(row["content"] or "")
        summary = content
        recommendation = meta.get("recommendation")
        if "\nRecommendation:" in content:
            summary, _, tail = content.partition("\nRecommendation:")
            summary = summary.strip()
            if not recommendation:
                recommendation = tail.strip()
        items.append({
            "reference": f"reflections:{row['id']}",
            "timestamp": row["timestamp"],
            "summary": summary,
            "recommendation": recommendation,
            "source_reference": meta.get("source_reference") or ((meta.get("provenance") or {}).get("source_reference") if isinstance(meta.get("provenance"), dict) else None),
            "provenance": provenance.preview_from_metadata(meta),
        })
    return {"ok": True, "items": items, **runtime}


def _ingest_request(request: IngestRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    content = request.content.strip() if isinstance(request.content, str) else ""
    if not content:
        return {"ok": False, "error": "empty_content", **runtime}

    kind = (request.kind or "experience").lower()
    if kind == "memory":
        memory_type = (request.memory_type or "knowledge").lower()
        allowed = {"knowledge", "reflections", "directives", "tasks", "runbooks", "lessons"}
        if memory_type not in allowed:
            memory_type = "knowledge"
        metadata = {
            "conversation_id": request.conversation_id,
            "session_id": request.session_id,
            "thread_id": request.thread_id,
            "message_id": request.message_id,
            "role": request.role,
            "source_reference": request.source_reference,
            "source_references": request.source_references,
            "source_label": request.source_label,
            "source_labels": request.source_labels,
            "transcript_path": request.transcript_path,
            "transcript_offset": request.transcript_offset,
            "transcript_end_offset": request.transcript_end_offset,
            "derived_via": "ingest",
        }
        memory_id = api.store_memory(
            memory_type,
            content,
            source=request.source,
            metadata=metadata,
            timestamp=request.timestamp,
        )
        reference = f"{memory_type}:{memory_id}"
        if request.conversation_id:
            memory_links.add_memory_link(reference, "conversation", f"conversation:{request.conversation_id}")
        if request.session_id:
            memory_links.add_memory_link(reference, "session", f"session:{request.session_id}")
        if request.thread_id:
            memory_links.add_memory_link(reference, "thread", f"thread:{request.thread_id}")
        if request.message_id:
            memory_links.add_memory_link(reference, "message", f"message:{request.message_id}")
        if request.transcript_path:
            if request.transcript_offset and request.transcript_end_offset and request.transcript_end_offset >= request.transcript_offset:
                suffix = f"#L{request.transcript_offset}-L{request.transcript_end_offset}"
            elif request.transcript_offset:
                suffix = f"#L{request.transcript_offset}"
            else:
                suffix = ""
            memory_links.add_memory_link(reference, "transcript", f"transcript:{request.transcript_path}{suffix}")
        if request.role:
            turn_response = _ingest_conversation_turn(
                ConversationTurnRequest(
                    role=request.role,
                    content=content,
                    conversation_id=request.conversation_id,
                    session_id=request.session_id,
                    thread_id=request.thread_id,
                    message_id=request.message_id,
                    source=request.source,
                    transcript_path=request.transcript_path,
                    transcript_offset=request.transcript_offset,
                    transcript_end_offset=request.transcript_end_offset,
                    timestamp=request.timestamp,
                    metadata={"memory_reference": reference},
                )
            )
        else:
            turn_response = None
        if turn_response and turn_response.get("reference"):
            provenance.update_memory_metadata(
                reference,
                {
                    "source_references": [
                        *([request.source_reference] if request.source_reference else []),
                        *(request.source_references or []),
                        str(turn_response.get("reference") or ""),
                    ]
                },
            )
        return {"ok": True, "kind": "memory", "memory_type": memory_type, "reference": reference, "turn": turn_response, **runtime}

    # experience ingest
    experience_metadata = provenance.normalize_metadata(
        {
            "conversation_id": request.conversation_id,
            "session_id": request.session_id,
            "thread_id": request.thread_id,
            "message_id": request.message_id,
            "role": request.role,
            "source_reference": request.source_reference,
            "source_references": request.source_references,
            "source_label": request.source_label,
            "source_labels": request.source_labels,
            "transcript_path": request.transcript_path,
            "transcript_offset": request.transcript_offset,
            "transcript_end_offset": request.transcript_end_offset,
            "task_id": request.task_id,
            "derived_via": "ingest",
        },
        source=request.source or "sidecar",
    )

    def _write_experience() -> None:
        conn = store.connect()
        try:
            conn.execute(
                "INSERT INTO experiences (task_id, outcome, reward_score, confidence, experience_type, source_module, metadata_json, schema_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request.task_id,
                    content,
                    None,
                    1.0,
                    "ingest",
                    request.source or "sidecar",
                    json.dumps(experience_metadata, ensure_ascii=False),
                    store.SCHEMA_VERSION,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    store.submit_write(_write_experience, timeout=30.0)
    return {"ok": True, "kind": "experience", **runtime}


@app.post("/memory/ingest")
def memory_ingest(request: IngestRequest) -> dict[str, Any]:
    return _ingest_request(request)


@app.post("/memory/ingest_async")
def memory_ingest_async(request: IngestRequest) -> dict[str, Any]:
    payload = request.dict()
    payload["queued_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    depth = _enqueue_payload(payload)
    return {"ok": True, "queued": True, "queueDepth": depth}


@app.get("/memory/ingest_status")
def memory_ingest_status() -> dict[str, Any]:
    return {"ok": True, "queueDepth": _queue_depth(), **QUEUE_STATS}


@app.post("/memory/ingest_flush")
def memory_ingest_flush(limit: int = 0) -> dict[str, Any]:
    stats = _drain_queue(limit if limit > 0 else None)
    return {"ok": True, "queueDepth": _queue_depth(), **stats}


@app.post("/memory/reinforce")
def memory_reinforce(request: ReinforceRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    result = reinforcement.log_experience(
        task_id=request.task_id,
        outcome=request.outcome,
        confidence=request.confidence,
        reward_score=request.reward_score,
        memory_reference=request.memory_reference,
        experience_type=request.experience_type,
        source_module=request.source_module,
    )
    if request.note:
        api.record_reinforcement(request.task_id, request.outcome, request.note, source_module=request.source_module)
    return {"ok": True, "result": result, **runtime}


@app.post("/memory/distill")
def memory_distill(request: DistillRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    results = distill.distill_experiences(limit=request.limit)
    return {"ok": True, "count": len(results), "results": results, **runtime}


@app.get("/metrics")
def metrics() -> dict[str, Any]:
    runtime = _runtime_payload()
    payload = health.get_memory_health()
    counts = payload.get("counts", {})
    counts["queue_depth"] = _queue_depth()
    counts["queue_processed"] = QUEUE_STATS.get("processed", 0)
    counts["queue_errors"] = QUEUE_STATS.get("errors", 0)
    payload["counts"] = counts
    coverage_tables = ["knowledge", "runbooks", "lessons", "directives", "reflections", "tasks"]
    conn = store.connect()
    try:
        payload["coverage"] = [
            {
                "table": table,
                "rows": int(counts.get(table, 0) or 0),
                "vectors": int(conn.execute("SELECT COUNT(*) FROM vector_embeddings WHERE source_type=?", (table,)).fetchone()[0] or 0),
                "missing": max(int(counts.get(table, 0) or 0) - int(conn.execute("SELECT COUNT(*) FROM vector_embeddings WHERE source_type=?", (table,)).fetchone()[0] or 0), 0),
            }
            for table in coverage_tables
        ]
    finally:
        conn.close()
    payload["queue"] = QUEUE_STATS
    return {"ok": True, "metrics": payload, **runtime}


def _event_stream():
    path = state_store.reports_dir() / "brain_memory.log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("")
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        handle.seek(0, 2)
        while True:
            line = handle.readline()
            if not line:
                time.sleep(0.5)
                continue
            yield f"data: {line.strip()}\n\n"


@app.get("/events")
def events() -> StreamingResponse:
    return StreamingResponse(_event_stream(), media_type="text/event-stream")


def _tail_events(limit: int = 50) -> str:
    path = state_store.reports_dir() / "brain_memory.log.jsonl"
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return ""
    return "\n".join(lines[-limit:])


@app.get("/dashboard")
def dashboard() -> HTMLResponse:
    metrics_payload = health.get_memory_health()
    counts = metrics_payload.get("counts", {})
    coverage_tables = ["knowledge", "runbooks", "lessons", "directives", "reflections", "tasks"]
    conn = store.connect()
    try:
        coverage_rows = []
        for table in coverage_tables:
            total = int(counts.get(table, 0) or 0)
            vectors = int(
                conn.execute(
                    "SELECT COUNT(*) FROM vector_embeddings WHERE source_type=?",
                    (table,),
                ).fetchone()[0]
                or 0
            )
            missing = max(total - vectors, 0)
            coverage_rows.append({"table": table, "rows": total, "vectors": vectors, "missing": missing})
    finally:
        conn.close()

    metrics_cards = [{"label": key, "value": value} for key, value in counts.items()]
    metrics_cards.extend(
        [
            {"label": "vector_index_count", "value": metrics_payload.get("vector_index_count", 0)},
            {"label": "vector_index_coverage", "value": metrics_payload.get("vector_index_coverage", 0)},
        ]
    )
    local_inference = metrics_payload.get("local_inference") or {}
    metrics_cards.extend(
        [
            {"label": "local_cache_hits", "value": local_inference.get("cache_hits", 0)},
            {"label": "local_warm_calls", "value": local_inference.get("warm_calls", 0)},
            {"label": "local_cold_calls", "value": local_inference.get("cold_calls", 0)},
            {"label": "local_success", "value": local_inference.get("local_success", 0)},
            {"label": "local_errors", "value": local_inference.get("local_errors", 0)},
            {"label": "frontier_calls_avoided_est", "value": local_inference.get("frontier_calls_avoided_est", 0)},
            {"label": "prompt_tokens_saved_est", "value": local_inference.get("prompt_tokens_saved_est", 0)},
            {"label": "completion_tokens_saved_est", "value": local_inference.get("completion_tokens_saved_est", 0)},
            {"label": "cost_saved_usd_est", "value": local_inference.get("cost_saved_usd_est", 0)},
        ]
    )
    metrics_html = "".join(
        f"<div class='card'><strong>{card['label']}</strong><br/>{card['value']}</div>" for card in metrics_cards
    )
    coverage_html = "".join(
        f"<div class='card'><strong>{row['table']}</strong><br/>rows: {row['rows']}<br/>vectors: {row['vectors']}<br/>missing: {row['missing']}</div>"
        for row in coverage_rows
    )
    events_html = _tail_events()

    html = f"""
    <!doctype html>
    <html>
    <head>
      <meta charset='utf-8'/>
      <title>ocmemog realtime</title>
      <style>
        body {{ font-family: system-ui, sans-serif; padding: 20px; }}
        .metrics {{ display: flex; gap: 12px; flex-wrap: wrap; }}
        .card {{ border: 1px solid #ddd; padding: 10px 14px; border-radius: 8px; min-width: 140px; }}
        pre {{ background: #f7f7f7; padding: 10px; height: 320px; overflow: auto; }}
      </style>
    </head>
    <body>
      <h2>ocmemog realtime</h2>
      <div class="metrics" id="metrics">{metrics_html}</div>
      <h3>Local cognition savings</h3>
      <div class="metrics" id="local-cognition">{local_html}</div>
      <h3>Vector coverage</h3>
      <div class="metrics" id="coverage">{coverage_html}</div>
      <h3>Ponder recommendations</h3>
      <div id="ponder-meta" style="margin-bottom:8px; color:#666;"></div>
      <div id="ponder"></div>
      <h3>Live events</h3>
      <pre id="events">{events_html}</pre>
      <script>
        const metricsEl = document.getElementById('metrics');
        const coverageEl = document.getElementById('coverage');
        const ponderEl = document.getElementById('ponder');
        const ponderMetaEl = document.getElementById('ponder-meta');
        const eventsEl = document.getElementById('events');

        async function refreshMetrics() {{
          const res = await fetch('/metrics');
          const data = await res.json();
          const counts = data.metrics?.counts || {{}};
          const cards = [
            ...Object.entries(counts).map(([k, v]) => ({{ label: k, value: v }})),
            {{ label: 'vector_index_count', value: data.metrics?.vector_index_count ?? 0 }},
            {{ label: 'vector_index_coverage', value: data.metrics?.vector_index_coverage ?? 0 }},
          ];
          metricsEl.innerHTML = cards.map((card) =>
            `<div class="card"><strong>${{card.label}}</strong><br/>${{card.value}}</div>`
          ).join('');
          const coverage = data.metrics?.coverage || [];
          coverageEl.innerHTML = coverage.map((row) =>
            `<div class="card"><strong>${{row.table}}</strong><br/>rows: ${{row.rows}}<br/>vectors: ${{row.vectors}}<br/>missing: ${{row.missing}}</div>`
          ).join('');
        }}

        async function refreshPonder() {{
          const res = await fetch('/memory/ponder/latest?limit=5');
          const data = await res.json();
          const items = data.items || [];
          const lastTs = items.length ? (items[0].timestamp || 'n/a') : 'n/a';
          const warnings = (data.warnings || []).join('; ');
          const mode = data.mode || 'n/a';
          ponderMetaEl.textContent = `Last update: ${{lastTs}} • Mode: ${{mode}}${{warnings ? ' • ' + warnings : ''}}`;
          ponderEl.innerHTML = items.map((item) =>
            `<div class="card"><strong>${{item.summary}}</strong><br/><em>${{item.recommendation || ''}}</em><br/><small>${{item.timestamp || ''}} • ${{item.reference || ''}}</small></div>`
          ).join('');
        }}

        refreshMetrics();
        refreshPonder();
        setInterval(refreshMetrics, 5000);
        setInterval(refreshPonder, 10000);

        const es = new EventSource('/events');
        es.onmessage = (ev) => {{
          eventsEl.textContent += ev.data + "\\n";
          eventsEl.scrollTop = eventsEl.scrollHeight;
        }};
      </script>
    </body>
    </html>
    """
    return HTMLResponse(html)
� Mode: ${{mode}}${{warnings ? ' • ' + warnings : ''}}`;
          ponderEl.innerHTML = items.map((item) =>
            `<div class="card"><strong>${{item.summary}}</strong><br/><em>${{item.recommendation || ''}}</em><br/><small>${{item.timestamp || ''}} • ${{item.reference || ''}}</small></div>`
          ).join('');
        }}

        refreshMetrics();
        refreshPonder();
        setInterval(refreshMetrics, 5000);
        setInterval(refreshPonder, 10000);

        const es = new EventSource('/events');
        es.onmessage = (ev) => {{
          eventsEl.textContent += ev.data + "\\n";
          eventsEl.scrollTop = eventsEl.scrollHeight;
        }};
      </script>
    </body>
    </html>
    """
    return HTMLResponse(html)
