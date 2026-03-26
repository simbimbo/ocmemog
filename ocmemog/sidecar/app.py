from __future__ import annotations

import json
import atexit
import faulthandler
import os
import re
import threading
import tempfile
import time
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from datetime import datetime, timedelta

from ocmemog import __version__
from ocmemog.runtime import state_store
from ocmemog.runtime.memory import (
    api,
    conversation_state,
    distill,
    health,
    memory_links,
    pondering_engine,
    provenance,
    reinforcement,
    retrieval,
    store,
    vector_index,
)
from ocmemog.sidecar.compat import flatten_results, probe_runtime
from ocmemog.sidecar.transcript_watcher import watch_forever

DEFAULT_CATEGORIES = tuple(store.MEMORY_TABLES)

API_TOKEN = os.environ.get("OCMEMOG_API_TOKEN")
_GOVERNANCE_REVIEW_CACHE_TTL_SECONDS = 15.0
_governance_review_cache: Dict[str, Any] = {"key": None, "expires_at": 0.0, "payload": None}


_BOOL_TRUE_VALUES = {"1", "true", "yes", "on", "y", "t"}
_BOOL_FALSE_VALUES = {"0", "false", "no", "off", "n", "f"}


def _default_openclaw_home() -> Path:
    explicit = os.environ.get("OPENCLAW_HOME", "").strip() or os.environ.get("OCMEMOG_OPENCLAW_HOME", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg:
        return (Path(xdg).expanduser() / "openclaw").resolve()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "").strip() or os.environ.get("LOCALAPPDATA", "").strip()
        if appdata:
            return (Path(appdata).expanduser() / "OpenClaw").resolve()
    return (Path.home() / ".openclaw").resolve()


def _default_transcript_root() -> Path:
    home = _default_openclaw_home()
    legacy = (Path.home() / ".openclaw" / "workspace" / "memory").resolve()
    if home == legacy.parent.parent:
        return legacy
    return home / "workspace" / "memory"


def _parse_bool_env_value(raw: Any | None, default: bool = False) -> tuple[bool, bool]:
    """Return ``(value, valid)``, where ``valid`` indicates parser confidence."""
    if raw is None:
        return default, True

    raw_value = str(raw).strip().lower()
    if raw_value in _BOOL_TRUE_VALUES:
        return True, True
    if raw_value in _BOOL_FALSE_VALUES:
        return False, True
    if not raw_value:
        return default, False
    return default, False


def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    value, _ = _parse_bool_env_value(raw, default=default)
    return value


def _parse_float_env(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.environ.get(name)
    try:
        value = float(raw if raw is not None else default)
    except Exception:
        print(
            f"[ocmemog][config] invalid float env value: {name}={raw!r}; using default {default}",
            file=sys.stderr,
        )
        return default
    if minimum is not None and value < minimum:
        print(
            f"[ocmemog][config] env value below minimum: {name}={value}; using default {default}",
            file=sys.stderr,
        )
        return default
    return value


def _parse_int_env(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.environ.get(name)
    try:
        value = int(raw if raw is not None else default)
    except Exception:
        print(
            f"[ocmemog][config] invalid int env value: {name}={raw!r}; using default {default}",
            file=sys.stderr,
        )
        return default
    if minimum is not None and value < minimum:
        print(
            f"[ocmemog][config] env value below minimum: {name}={value}; using default {default}",
            file=sys.stderr,
        )
        return default
    return value


_SHUTDOWN_TIMING = _parse_bool_env("OCMEMOG_SHUTDOWN_TIMING", default=True)
_QUEUE_RETRY_KEY = "_ocmemog_retry_count"


@asynccontextmanager
async def _sidecar_lifespan(_: FastAPI):
    _startup_started = time.perf_counter()
    try:
        _start_transcript_watcher()
        _start_ingest_worker()
        if _SHUTDOWN_TIMING:
            print(
                f"[ocmemog][shutdown] lifespan_startup elapsed={time.perf_counter()-_startup_started:.3f}s",
                file=sys.stderr,
            )
        yield
    finally:
        shutdown_started = time.perf_counter()
        _stop_background_workers()
        if _SHUTDOWN_TIMING:
            print(
                f"[ocmemog][shutdown] lifespan_shutdown elapsed={time.perf_counter()-shutdown_started:.3f}s",
                file=sys.stderr,
            )


app = FastAPI(title="ocmemog sidecar", version=__version__, lifespan=_sidecar_lifespan)

_INGEST_WORKER_STOP = threading.Event()
_INGEST_WORKER_THREAD: threading.Thread | None = None
_INGEST_WORKER_LOCK = threading.Lock()
_WATCHER_STOP = threading.Event()
_WATCHER_THREAD: threading.Thread | None = None
_WATCHER_LOCK = threading.Lock()
_HYDRATE_CACHE_LOCK = threading.Lock()
_HYDRATE_CACHE: dict[tuple[str, str, str, int, int], tuple[float, dict[str, Any]]] = {}
QUEUE_LOCK = threading.Lock()
QUEUE_PROCESS_LOCK = threading.Lock()
QUEUE_STATS = {
    "last_run": None,
    "processed": 0,
    "errors": 0,
    "last_error": None,
    "last_batch": 0,
}
_POSTPROCESS_TASK_KEY = "_ocmemog_task"
_POSTPROCESS_TASK_VALUE = "postprocess_memory"


_REFLECTION_RECLASSIFY_PREFERENCE_PATTERNS = (
    re.compile(r"\b(?:i|we)\s+(?:prefer|like|love|enjoy)\b", re.IGNORECASE),
    re.compile(r"\b(?:i|we)\s+(?:dislike|hate|avoid)\b", re.IGNORECASE),
    re.compile(r"\bmy favorite\b", re.IGNORECASE),
    re.compile(r"\b(?:the )?user\s+(?:prefers|likes|loves|enjoys|dislikes|hates|avoids)\b", re.IGNORECASE),
)
_REFLECTION_RECLASSIFY_IDENTITY_PATTERNS = (
    re.compile(r"\b(?:i am|i'm)\s+(?!thinking\b|trying\b|working on\b|going to\b|not\b)", re.IGNORECASE),
    re.compile(r"\bmy name is\b", re.IGNORECASE),
    re.compile(r"\b(?:i|we)\s+(?:live|work)\s+in\b", re.IGNORECASE),
    re.compile(r"\b(?:i|we)\s+(?:work|study)\s+at\b", re.IGNORECASE),
    re.compile(r"\bmy pronouns are\b", re.IGNORECASE),
    re.compile(r"\bmy (?:time zone|timezone) is\b", re.IGNORECASE),
    re.compile(r"\b(?:the )?user\s+(?:is|works|lives)\b", re.IGNORECASE),
)
_REFLECTION_RECLASSIFY_FACT_PATTERNS = (
    re.compile(r"\b(?:i|we)\s+use\b", re.IGNORECASE),
    re.compile(r"\b(?:i|we)\s+have\b", re.IGNORECASE),
    re.compile(r"\b(?:i am|i'm)\s+allergic to\b", re.IGNORECASE),
    re.compile(r"\bmy (?:birthday|email|phone number) is\b", re.IGNORECASE),
    re.compile(r"\b(?:the )?user\s+has\b", re.IGNORECASE),
)
_REFLECTION_RECLASSIFY_BLOCKLIST_PATTERNS = (
    re.compile(r"\b(?:reflect|reflection|reflections)\b", re.IGNORECASE),
    re.compile(r"\b(?:i think|i wonder|maybe|perhaps)\b", re.IGNORECASE),
    re.compile(r"\?$"),
)


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
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(QUEUE_STATS, indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=str(path.parent), prefix='queue_stats.', suffix='.tmp', delete=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        tmp_name = handle.name
    Path(tmp_name).replace(path)


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    if API_TOKEN:
        header = request.headers.get("x-ocmemog-token") or request.headers.get("authorization", "")
        token = header.replace("Bearer ", "") if header else ""
        if token != API_TOKEN:
            return JSONResponse(status_code=401, content={"ok": False, "error": "unauthorized"})
    return await call_next(request)


def _watcher_direct_turn_ingest(payload: dict) -> bool:
    try:
        request = ConversationTurnRequest(**payload)
        response = _ingest_conversation_turn(request)
        return bool(response.get("ok"))
    except Exception as exc:
        print(f"[ocmemog][watcher] direct_turn_ingest_failed error={exc!r}", file=sys.stderr)
        return False



def _start_transcript_watcher() -> None:
    global _WATCHER_THREAD
    _load_queue_stats()
    enabled = _parse_bool_env("OCMEMOG_TRANSCRIPT_WATCHER")
    if not enabled:
        return
    with _WATCHER_LOCK:
        if _WATCHER_THREAD and _WATCHER_THREAD.is_alive():
            return
        _WATCHER_STOP.clear()
        _WATCHER_THREAD = threading.Thread(
            target=watch_forever,
            args=(_WATCHER_STOP, _watcher_direct_turn_ingest),
            daemon=True,
            name="ocmemog-transcript-watcher",
        )
        _WATCHER_THREAD.start()


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
            continue
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


def _enqueue_postprocess(reference: str, *, skip_embedding_provider: bool = True) -> int:
    return _enqueue_payload({
        _POSTPROCESS_TASK_KEY: _POSTPROCESS_TASK_VALUE,
        "reference": reference,
        "skip_embedding_provider": bool(skip_embedding_provider),
    })


def _run_postprocess_payload(payload: Dict[str, Any]) -> None:
    started = time.perf_counter()
    reference = str(payload.get("reference") or "").strip()
    if not reference:
        raise ValueError("missing_reference")
    skip_embedding_provider = bool(payload.get("skip_embedding_provider", True))
    result = api.postprocess_stored_memory(reference, skip_embedding_provider=skip_embedding_provider)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    trace = _parse_bool_env("OCMEMOG_TRACE_INGEST_PIPELINE", default=False)
    warn_ms = _parse_float_env("OCMEMOG_TRACE_INGEST_PIPELINE_WARN_MS", default=20.0, minimum=0.0)
    if trace or elapsed_ms >= warn_ms:
        print(f"[ocmemog][ingest] postprocess elapsed_ms={elapsed_ms:.3f} reference={reference}", file=sys.stderr)
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "postprocess_failed"))


def _should_link_ingest_memory_to_turn(request: IngestRequest) -> bool:
    source = str(request.source or "").strip().lower()
    if source in {"session", "transcript"}:
        return False
    return True



def _process_queue(limit: Optional[int] = None) -> Dict[str, Any]:
    processed = 0
    errors = 0
    last_error = None
    batch_limit = limit if limit is not None and limit > 0 else None
    max_retries = _parse_int_env("OCMEMOG_INGEST_MAX_RETRIES", default=3, minimum=1)

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

            acknowledged = 0
            requeue_payload: Dict[str, Any] | None = None
            for line_no, payload in batch:
                try:
                    if isinstance(payload, dict) and payload.get(_POSTPROCESS_TASK_KEY) == _POSTPROCESS_TASK_VALUE:
                        _run_postprocess_payload(payload)
                    else:
                        req = IngestRequest(**payload)
                        _ingest_request(req)
                    processed += 1
                    acknowledged = line_no
                except Exception as exc:
                    errors += 1
                    last_error = str(exc)
                    retry_count = 0
                    if isinstance(payload, dict):
                        try:
                            retry_count = int(payload.get(_QUEUE_RETRY_KEY, 0) or 0)
                        except Exception:
                            retry_count = 0
                    retry_count += 1
                    if retry_count >= max_retries:
                        acknowledged = line_no
                        QUEUE_STATS["last_error"] = f"{last_error} (dropped_after_retries={retry_count})"
                    elif isinstance(payload, dict):
                        acknowledged = line_no
                        requeue_payload = dict(payload)
                        requeue_payload[_QUEUE_RETRY_KEY] = retry_count
                    break

            if not errors:
                acknowledged = consumed_lines
            _ack_queue_batch(acknowledged)
            if requeue_payload is not None:
                _enqueue_payload(requeue_payload)

            if errors:
                break
            if not batch:
                break

    QUEUE_STATS["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
    QUEUE_STATS["last_batch"] = processed
    QUEUE_STATS["processed"] += processed
    if errors:
        QUEUE_STATS["errors"] += errors
    if last_error and "dropped_after_retries=" not in str(QUEUE_STATS.get("last_error") or ""):
        QUEUE_STATS["last_error"] = last_error
    _save_queue_stats()
    return {"processed": processed, "errors": errors, "last_error": QUEUE_STATS.get("last_error") or last_error}



def _ingest_worker() -> None:
    enabled = _parse_bool_env("OCMEMOG_INGEST_ASYNC_WORKER", default=True)
    if not enabled:
        return
    poll_seconds = _parse_float_env("OCMEMOG_INGEST_ASYNC_POLL_SECONDS", default=5.0, minimum=0.0)
    batch_max = _parse_int_env("OCMEMOG_INGEST_ASYNC_BATCH_MAX", default=25, minimum=1)

    while not _INGEST_WORKER_STOP.is_set():
        _process_queue(batch_max)
        if _INGEST_WORKER_STOP.wait(poll_seconds):
            break



def _drain_queue(limit: Optional[int] = None) -> Dict[str, Any]:
    return _process_queue(limit)


def _start_ingest_worker() -> None:
    global _INGEST_WORKER_THREAD
    with _INGEST_WORKER_LOCK:
        if _INGEST_WORKER_THREAD and _INGEST_WORKER_THREAD.is_alive():
            return
        _INGEST_WORKER_STOP.clear()
        _INGEST_WORKER_THREAD = threading.Thread(
            target=_ingest_worker,
            daemon=True,
            name="ocmemog-ingest-worker",
        )
        _INGEST_WORKER_THREAD.start()


def _stop_background_workers() -> None:
    global _INGEST_WORKER_THREAD, _WATCHER_THREAD
    shutdown_start = time.perf_counter()
    if _SHUTDOWN_TIMING:
        print(f"[ocmemog][shutdown] shutdown_begin", file=sys.stderr)
    timeout = _parse_float_env(
        "OCMEMOG_WORKER_SHUTDOWN_TIMEOUT_SECONDS",
        default=0.35,
        minimum=0.0,
    )
    if _SHUTDOWN_TIMING:
        print(f"[ocmemog][shutdown] shutdown_config timeout={timeout:.3f}s", file=sys.stderr)

    queue_drain_requested = _parse_bool_env("OCMEMOG_SHUTDOWN_DRAIN_QUEUE")
    if queue_drain_requested and _queue_depth() > 0:
        _queue_drain_start = time.perf_counter()
        drain_stats = _drain_queue()
        if _SHUTDOWN_TIMING:
            print(
                f"[ocmemog][shutdown] queue_drain elapsed={time.perf_counter()-_queue_drain_start:.3f}s processed={drain_stats.get('processed', 0)} errors={drain_stats.get('errors', 0)}",
                file=sys.stderr,
            )
    _INGEST_WORKER_STOP.set()
    _WATCHER_STOP.set()
    if _SHUTDOWN_TIMING:
        print(
            f"[ocmemog][shutdown] stop_signals_set elapsed={time.perf_counter()-shutdown_start:.3f}s",
            file=sys.stderr,
        )

    if _parse_bool_env("OCMEMOG_SHUTDOWN_DUMP_THREADS"):
        _dump_thread_dump("post-stop requested")

    with _INGEST_WORKER_LOCK:
        ingest_worker = _INGEST_WORKER_THREAD
    if ingest_worker is not None and ingest_worker.is_alive():
        ingest_join_start = time.perf_counter()
        ingest_worker.join(timeout=timeout)
        if _SHUTDOWN_TIMING:
            print(
                f"[ocmemog][shutdown] ingest_worker_join elapsed={time.perf_counter()-ingest_join_start:.3f}s alive={ingest_worker.is_alive()}",
                file=sys.stderr,
            )
        if _parse_bool_env("OCMEMOG_SHUTDOWN_DUMP_THREADS"):
            _dump_join_result("ingest-worker", ingest_worker, timeout)
        if not ingest_worker.is_alive():
            with _INGEST_WORKER_LOCK:
                if _INGEST_WORKER_THREAD is ingest_worker:
                    _INGEST_WORKER_THREAD = None

    with _WATCHER_LOCK:
        watcher_thread = _WATCHER_THREAD
    if watcher_thread is not None and watcher_thread.is_alive():
        watcher_join_start = time.perf_counter()
        watcher_thread.join(timeout=timeout)
        if _SHUTDOWN_TIMING:
            print(
                f"[ocmemog][shutdown] transcript_watcher_join elapsed={time.perf_counter()-watcher_join_start:.3f}s alive={watcher_thread.is_alive()}",
                file=sys.stderr,
            )
    if _parse_bool_env("OCMEMOG_SHUTDOWN_DUMP_THREADS"):
        _dump_join_result("transcript-watcher", watcher_thread, timeout)
        if not watcher_thread.is_alive():
            with _WATCHER_LOCK:
                if _WATCHER_THREAD is watcher_thread:
                    _WATCHER_THREAD = None
    if _SHUTDOWN_TIMING:
        print(
            f"[ocmemog][shutdown] shutdown_complete elapsed={time.perf_counter()-shutdown_start:.3f}s",
            file=sys.stderr,
        )


def _dump_thread_dump(context: str) -> None:
    print(f"[ocmemog][thread-dump:{context}]", file=sys.stderr)
    _dump_thread_states()
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)


def _dump_join_result(thread_label: str, thread: threading.Thread, timeout: float) -> None:
    if thread.is_alive():
        print(
            f"[ocmemog][shutdown] {thread_label} still alive after join timeout={timeout:.3f}s",
            file=sys.stderr,
        )
        _dump_thread_dump(thread_label)
    else:
        print(
            f"[ocmemog][shutdown] {thread_label} joined cleanly",
            file=sys.stderr,
        )


def _dump_thread_states() -> None:
    for thread in threading.enumerate():
        print(
            f"[ocmemog][thread-state] name={thread.name} alive={thread.is_alive()} daemon={thread.daemon} ident={thread.ident}",
            file=sys.stderr,
        )


atexit.register(_stop_background_workers)


class SearchRequest(BaseModel):
    query: str = Field(default="")
    limit: int = Field(default=5, ge=1, le=50)
    categories: Optional[List[str]] = None
    metadata_filters: Optional[Dict[str, Any]] = None
    lane: Optional[str] = Field(default=None, description="Optional retrieval lane/domain hint, e.g. 'tbc'")


class DuplicateCandidatesRequest(BaseModel):
    reference: str
    limit: int = Field(default=5, ge=1, le=25)
    min_similarity: float = Field(default=0.72, ge=0.1, le=1.0)


class ContradictionCandidatesRequest(BaseModel):
    reference: str
    limit: int = Field(default=5, ge=1, le=25)
    min_signal: float = Field(default=0.55, ge=0.1, le=1.0)
    use_model: bool = True


class GovernanceCandidatesRequest(BaseModel):
    categories: Optional[List[str]] = None
    limit: int = Field(default=50, ge=1, le=200)


class GovernanceReviewRequest(BaseModel):
    categories: Optional[List[str]] = None
    limit: int = Field(default=100, ge=1, le=500)
    context_depth: int = Field(default=1, ge=0, le=2)
    scan_limit: int = Field(default=3000, ge=1, le=10000)


class GovernanceDecisionRequest(BaseModel):
    reference: str
    relationship: str
    target_reference: str
    approved: bool = True


class AutoHydrationPolicyRequest(BaseModel):
    agent_id: Optional[str] = None


class GovernanceReviewDecisionRequest(BaseModel):
    reference: str
    target_reference: str
    approved: bool = True
    kind: Optional[str] = None
    relationship: Optional[str] = None
    context_depth: int = Field(default=1, ge=0, le=2)


class GovernanceSummaryRequest(BaseModel):
    categories: Optional[List[str]] = None


class GovernanceQueueRequest(BaseModel):
    categories: Optional[List[str]] = None
    limit: int = Field(default=100, ge=1, le=500)


class GovernanceAuditRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=500)
    kinds: Optional[List[str]] = None


class GovernanceAutoResolveRequest(BaseModel):
    categories: Optional[List[str]] = None
    limit: int = Field(default=20, ge=1, le=200)
    dry_run: bool = True
    profile: Optional[str] = None


class GovernanceRollbackRequest(BaseModel):
    reference: str
    relationship: str
    target_reference: str


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
    memory_type: Optional[str] = Field(default=None, description="knowledge|preferences|identity|reflections|directives|tasks|runbooks|lessons")
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
    metadata: Optional[Dict[str, Any]] = None


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
    predictive_brief_limit: int = Field(default=5, ge=1, le=12)


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


def _parse_agent_id_list(raw: str | None) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _auto_hydration_policy(agent_id: str | None = None) -> dict[str, Any]:
    normalized = str(agent_id or "").strip() or None
    allow_agent_ids = _parse_agent_id_list(os.environ.get("OCMEMOG_AUTO_HYDRATION_ALLOW_AGENT_IDS"))
    deny_agent_ids = _parse_agent_id_list(os.environ.get("OCMEMOG_AUTO_HYDRATION_DENY_AGENT_IDS"))
    enabled = _parse_bool_env("OCMEMOG_AUTO_HYDRATION", default=False)
    if not enabled:
        reason = "disabled_globally"
        allowed = False
    elif normalized and normalized in deny_agent_ids:
        reason = "denied_by_agent_id"
        allowed = False
    elif allow_agent_ids:
        allowed = bool(normalized and normalized in allow_agent_ids)
        reason = "allowed_by_allowlist" if allowed else "not_in_allowlist"
    else:
        reason = "allowed_globally"
        allowed = True
    return {
        "enabled": enabled,
        "allowed": allowed,
        "reason": reason,
        "agent_id": normalized,
        "allow_agent_ids": allow_agent_ids,
        "deny_agent_ids": deny_agent_ids,
        "scoped_by_agent": bool(allow_agent_ids or deny_agent_ids),
    }


def _runtime_payload() -> Dict[str, Any]:
    status = probe_runtime()
    return {
        "mode": status.mode,
        "missingDeps": status.missing_deps,
        "identity": status.identity,
        "capabilities": status.capabilities,
        "todo": status.todo,
        "warnings": status.warnings,
        "runtimeSummary": status.runtime_summary,
    }


def _retune_reflection_memory_type(content: str, memory_type: str) -> str:
    if memory_type != "reflections":
        return memory_type
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    if not text or len(text) > 280:
        return memory_type
    if any(pattern.search(text) for pattern in _REFLECTION_RECLASSIFY_BLOCKLIST_PATTERNS):
        return memory_type
    if any(pattern.search(text) for pattern in _REFLECTION_RECLASSIFY_PREFERENCE_PATTERNS):
        return "preferences"
    if any(pattern.search(text) for pattern in _REFLECTION_RECLASSIFY_IDENTITY_PATTERNS):
        return "identity"
    if any(pattern.search(text) for pattern in _REFLECTION_RECLASSIFY_FACT_PATTERNS):
        return "identity"
    return memory_type


def _fallback_search(
    query: str,
    limit: int,
    categories: List[str],
    *,
    metadata_filters: Optional[Dict[str, Any]] = None,
    lane: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conn = store.connect()
    active_lane = retrieval.infer_lane(query, explicit_lane=lane)
    try:
        results: List[Dict[str, Any]] = []
        for table in categories:
            rows = conn.execute(
                f"SELECT id, content, confidence, metadata_json FROM {table} WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{query}%", limit * 5),
            ).fetchall()
            for row in rows:
                meta = json.loads(row["metadata_json"] or "{}") if row["metadata_json"] else {}
                if not retrieval._metadata_matches(meta, metadata_filters):
                    continue
                lane_bonus = retrieval._lane_bonus(meta, active_lane)
                results.append(
                    {
                        "bucket": table,
                        "reference": f"{table}:{row['id']}",
                        "table": table,
                        "id": str(row["id"]),
                        "content": str(row["content"] or ""),
                        "score": float(row["confidence"] or 0.0) + lane_bonus,
                        "links": [],
                        "metadata": meta,
                    }
                )
        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:limit]
    finally:
        conn.close()


def _compact_text(value: Any, max_len: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > max_len:
        return f"{text[: max_len - 1].rstrip()}…"
    return text


def _build_predictive_brief(
    *,
    request: ConversationHydrateRequest,
    turns: Sequence[Dict[str, Any]],
    summary: Dict[str, Any],
    linked_memories: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    latest_user_ask = ((summary.get("latest_user_intent") or {}).get("effective_content") if isinstance(summary.get("latest_user_intent"), dict) else None) or ((summary.get("latest_user_ask") or {}).get("content") if isinstance(summary.get("latest_user_ask"), dict) else None) or ""
    summary_text = str(summary.get("summary_text") or "").strip()
    query = _compact_text(latest_user_ask or summary_text or "resume context", 260)
    lane = retrieval.infer_lane(query)
    profiles = retrieval._load_lane_profiles()
    profile = profiles.get(lane or "") if lane else None
    metadata_filters = profile.get("metadata_filters") if isinstance(profile, dict) else None
    categories = ["knowledge", "runbooks", "tasks", "reflections", "directives"]
    retrieved = retrieval.retrieve_for_queries(
        [query],
        limit=max(1, request.predictive_brief_limit),
        categories=categories,
        metadata_filters=metadata_filters,
        lane=lane,
        skip_vector_provider=True,
    )
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for bucket in categories:
        for item in retrieved.get(bucket, []) or []:
            ref = str(item.get("reference") or "")
            if not ref or ref in seen:
                continue
            seen.add(ref)
            items.append(
                {
                    "reference": ref,
                    "category": bucket,
                    "content": _compact_text(item.get("content") or "", 180),
                    "selected_because": item.get("selected_because") or item.get("retrieval_signals") or "retrieval",
                    "score": item.get("score"),
                    "metadata": item.get("metadata") or {},
                }
            )
            if len(items) >= request.predictive_brief_limit:
                break
        if len(items) >= request.predictive_brief_limit:
            break

    checkpoint = summary.get("latest_checkpoint") if isinstance(summary.get("latest_checkpoint"), dict) else None
    open_loops = summary.get("open_loops") if isinstance(summary.get("open_loops"), list) else []
    recent_linked = []
    for item in linked_memories[:2]:
        if not isinstance(item, dict):
            continue
        recent_linked.append({
            "reference": item.get("reference"),
            "summary": _compact_text(item.get("summary") or item.get("content") or item.get("reference") or "", 140),
        })
    return {
        "lane": lane,
        "query": query,
        "metadata_filters": metadata_filters or {},
        "checkpoint": {
            "reference": checkpoint.get("reference") if checkpoint else None,
            "summary": _compact_text(checkpoint.get("summary") if checkpoint else "", 180),
        } if checkpoint else None,
        "open_loops": [
            {
                "kind": item.get("kind"),
                "summary": _compact_text(item.get("summary") or "", 120),
                "reference": item.get("source_reference") or item.get("reference"),
            }
            for item in open_loops[:2]
            if isinstance(item, dict) and str(item.get("summary") or "").strip()
        ],
        "memories": items,
        "linked_memories": recent_linked,
        "latest_user_ask": _compact_text(latest_user_ask, 180),
        "summary_text": _compact_text(summary_text, 220),
        "mode": "predictive-brief",
    }


_ALLOWED_MEMORY_REFERENCE_TYPES = {
    *store.MEMORY_TABLES,
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
    if prefix in {*store.MEMORY_TABLES, "conversation_turns", "conversation_checkpoints"} and not identifier.isdigit():
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
        roots = [_default_transcript_root()]
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
    payload["ready"] = payload.get("mode") == "ready"
    return payload


@app.post("/memory/search")
def memory_search(request: SearchRequest) -> dict[str, Any]:
    categories = _normalize_categories(request.categories)
    runtime = _runtime_payload()
    started = time.perf_counter()
    query = request.query or ""
    active_lane = retrieval.infer_lane(query, explicit_lane=request.lane)
    skip_vector_provider = _parse_bool_env("OCMEMOG_SEARCH_SKIP_EMBEDDING_PROVIDER", default=True)
    provider_hint = (
        os.environ.get("OCMEMOG_EMBED_MODEL_PROVIDER", "").strip()
        or os.environ.get("OCMEMOG_EMBED_PROVIDER", "").strip()
        or os.environ.get("BRAIN_EMBED_MODEL_PROVIDER", "").strip()
    )
    diagnostics = {
        "strategy": "hybrid",
        "fallback": False,
        "skip_vector_provider": bool(skip_vector_provider),
        "lane": active_lane,
        "metadata_filter_keys": sorted((request.metadata_filters or {}).keys()),
        "requested_limit": int(request.limit),
        "categories": list(categories),
        "execution_path": {
            "semantic_search_enabled": True,
            "provider_configured": bool(provider_hint),
            "provider_name": provider_hint or None,
            "provider_skipped_by_request": bool(skip_vector_provider and provider_hint),
            "local_semantic_fallback_expected": bool(skip_vector_provider or not provider_hint),
            "route_exception_fallback": False,
        },
    }
    try:
        results = retrieval.retrieve_for_queries(
            [query],
            limit=request.limit,
            categories=categories,
            skip_vector_provider=skip_vector_provider,
            metadata_filters=request.metadata_filters,
            lane=request.lane,
        )
        flattened = flatten_results(results)
        bucket_counts = {bucket: len(results.get(bucket, [])) for bucket in categories}
        diagnostics["bucket_counts"] = bucket_counts
        diagnostics["result_count_before_compaction"] = len(flattened)
        if len(flattened) > request.limit:
            flattened = flattened[: request.limit]
        diagnostics["result_count"] = len(flattened)
        governance_rollup = {
            "status_counts": {},
            "needs_review_count": 0,
            "by_bucket": {},
        }
        for item in flattened:
            summary = item.get("governance_summary") if isinstance(item, dict) else {}
            if not isinstance(summary, dict):
                summary = {}
            status = str(summary.get("memory_status") or item.get("memory_status") or "active")
            governance_rollup["status_counts"][status] = governance_rollup["status_counts"].get(status, 0) + 1
            bucket = str(item.get("bucket") or item.get("category") or item.get("source_type") or "unknown")
            bucket_rollup = governance_rollup["by_bucket"].setdefault(bucket, {"status_counts": {}, "needs_review_count": 0})
            bucket_rollup["status_counts"][status] = bucket_rollup["status_counts"].get(status, 0) + 1
            if bool(summary.get("needs_review")):
                governance_rollup["needs_review_count"] += 1
                bucket_rollup["needs_review_count"] += 1
        diagnostics["governance_rollup"] = governance_rollup
        reinforcement_rollup = {
            "reinforced_result_count": 0,
            "negative_reinforcement_result_count": 0,
            "total_reinforcement_count": 0.0,
            "total_negative_penalty": 0.0,
            "by_bucket": {},
        }
        for item in flattened:
            signals = item.get("retrieval_signals") if isinstance(item, dict) else {}
            if not isinstance(signals, dict):
                signals = {}
            reinforcement_count = float(signals.get("reinforcement_count") or 0.0)
            negative_penalty = float(signals.get("reinforcement_negative_penalty") or 0.0)
            if reinforcement_count <= 0.0 and negative_penalty <= 0.0:
                continue
            bucket = str(item.get("bucket") or item.get("category") or item.get("source_type") or "unknown")
            bucket_rollup = reinforcement_rollup["by_bucket"].setdefault(
                bucket,
                {
                    "reinforced_result_count": 0,
                    "negative_reinforcement_result_count": 0,
                    "total_reinforcement_count": 0.0,
                    "total_negative_penalty": 0.0,
                },
            )
            if reinforcement_count > 0.0:
                reinforcement_rollup["reinforced_result_count"] += 1
                reinforcement_rollup["total_reinforcement_count"] += reinforcement_count
                bucket_rollup["reinforced_result_count"] += 1
                bucket_rollup["total_reinforcement_count"] += reinforcement_count
            if negative_penalty > 0.0:
                reinforcement_rollup["negative_reinforcement_result_count"] += 1
                reinforcement_rollup["total_negative_penalty"] += negative_penalty
                bucket_rollup["negative_reinforcement_result_count"] += 1
                bucket_rollup["total_negative_penalty"] += negative_penalty
        diagnostics["reinforcement_rollup"] = reinforcement_rollup
        used_fallback = False
    except Exception as exc:
        flattened = _fallback_search(request.query, request.limit, categories, metadata_filters=request.metadata_filters, lane=request.lane)
        diagnostics.update(
            {
                "strategy": "fallback-like",
                "fallback": True,
                "bucket_counts": {},
                "result_count_before_compaction": len(flattened),
                "result_count": len(flattened),
                "fallback_reason": type(exc).__name__,
            }
        )
        diagnostics["execution_path"]["route_exception_fallback"] = True
        used_fallback = True
        runtime["warnings"] = [*runtime["warnings"], f"search fallback enabled: {exc}"]
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    diagnostics["elapsed_ms"] = elapsed_ms
    diagnostics["query_length"] = len(query)
    diagnostics["query_token_count"] = len(retrieval._tokenize(query))
    vector_diagnostics = vector_index.get_last_search_diagnostics()
    if vector_diagnostics:
        diagnostics["vector_search"] = vector_diagnostics
        embedding_diag = vector_diagnostics.get("embedding") if isinstance(vector_diagnostics, dict) else None
        if isinstance(embedding_diag, dict):
            diagnostics["execution_path"].update(
                {
                    "provider_attempted": bool(embedding_diag.get("provider_attempted")),
                    "embedding_generated": bool(embedding_diag.get("embedding_generated")),
                    "embedding_path_used": embedding_diag.get("path_used"),
                    "local_fallback_used": bool(embedding_diag.get("local_used")),
                }
            )
    retrieval_diagnostics = retrieval.get_last_retrieval_diagnostics()
    if retrieval_diagnostics:
        diagnostics["retrieval_governance"] = {
            "suppressed_by_governance": retrieval_diagnostics.get("suppressed_by_governance") or {},
            "suppressed_by_governance_by_bucket": retrieval_diagnostics.get("suppressed_by_governance_by_bucket") or {},
        }
        diagnostics["retrieval_reinforcement"] = retrieval_diagnostics.get("reinforcement") or {}
    if elapsed_ms >= 10:
        print(
            f"[ocmemog][route] memory_search elapsed_ms={elapsed_ms:.3f} limit={request.limit} categories={','.join(categories)} fallback={used_fallback}",
            file=sys.stderr,
        )
        if elapsed_ms >= 200:
            print(
                f"[ocmemog][route] memory_search slow_path query={query[:128]!r} result_count={len(flattened)}",
                file=sys.stderr,
            )

    return {
        "ok": True,
        "query": request.query,
        "limit": request.limit,
        "categories": categories,
        "results": flattened,
        "usedFallback": used_fallback,
        "searchDiagnostics": diagnostics,
        **runtime,
    }


@app.post("/memory/duplicate_candidates")
def memory_duplicate_candidates(request: DuplicateCandidatesRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    candidates = api.find_duplicate_candidates(
        request.reference,
        limit=request.limit,
        min_similarity=request.min_similarity,
    )
    return {
        "ok": True,
        "reference": request.reference,
        "limit": request.limit,
        "min_similarity": request.min_similarity,
        "candidates": candidates,
        **runtime,
    }


@app.post("/memory/contradiction_candidates")
def memory_contradiction_candidates(request: ContradictionCandidatesRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    candidates = api.find_contradiction_candidates(
        request.reference,
        limit=request.limit,
        min_signal=request.min_signal,
        use_model=request.use_model,
    )
    return {
        "ok": True,
        "reference": request.reference,
        "limit": request.limit,
        "min_signal": request.min_signal,
        "use_model": request.use_model,
        "candidates": candidates,
        **runtime,
    }


@app.post("/memory/governance/candidates")
def memory_governance_candidates(request: GovernanceCandidatesRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    items = api.list_governance_candidates(categories=request.categories, limit=request.limit)
    return {
        "ok": True,
        "categories": request.categories,
        "limit": request.limit,
        "items": items,
        **runtime,
    }


@app.post("/memory/governance/review")
def memory_governance_review(request: GovernanceReviewRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    items = api.list_governance_review_items(
        categories=request.categories,
        limit=request.limit,
        context_depth=request.context_depth,
        scan_limit=request.scan_limit,
    )
    return {
        "ok": True,
        "categories": request.categories,
        "limit": request.limit,
        "context_depth": request.context_depth,
        "scan_limit": request.scan_limit,
        "items": items,
        **runtime,
    }


@app.post("/memory/governance/review/summary")
def memory_governance_review_summary(request: GovernanceReviewRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    limit = min(int(request.limit or 25), 50)
    scan_limit = min(int(request.scan_limit or max(limit * 10, 250)), 500)
    cache_key = json.dumps(
        {
            "categories": sorted(request.categories or []),
            "limit": limit,
            "context_depth": 0,
            "scan_limit": scan_limit,
        },
        sort_keys=True,
    )
    now = time.time()
    expires_at = float(_governance_review_cache.get("expires_at") or 0.0)
    if _governance_review_cache.get("key") == cache_key and expires_at > now:
        cached_payload = _governance_review_cache.get("payload") or {}
        diagnostics = dict(cached_payload.get("reviewDiagnostics") or {})
        diagnostics.update(
            {
                "cache_hit": True,
                "cache_ttl_seconds": round(max(0.0, expires_at - now), 3),
            }
        )
        return {**cached_payload, **runtime, "cached": True, "reviewDiagnostics": diagnostics}

    items = api.list_governance_review_items(
        categories=request.categories,
        limit=limit,
        context_depth=0,
        scan_limit=scan_limit,
    )
    kind_counts: Dict[str, int] = {}
    priority_label_counts: Dict[str, int] = {}
    for item in items:
        item_kind = str(item.get("kind") or "unknown")
        kind_counts[item_kind] = kind_counts.get(item_kind, 0) + 1
        priority_label = str(item.get("priority_label") or "unknown")
        priority_label_counts[priority_label] = priority_label_counts.get(priority_label, 0) + 1
    diagnostics = {
        "cache_hit": False,
        "cache_ttl_seconds": round(float(_GOVERNANCE_REVIEW_CACHE_TTL_SECONDS), 3),
        "item_count": len(items),
        "kind_counts": kind_counts,
        "priority_label_counts": priority_label_counts,
        "filters": {
            "categories": list(request.categories or []),
            "limit": limit,
            "context_depth": 0,
            "scan_limit": scan_limit,
        },
    }
    payload = {
        "ok": True,
        "categories": request.categories,
        "limit": limit,
        "context_depth": 0,
        "scan_limit": scan_limit,
        "items": items,
        "cached": False,
        "reviewDiagnostics": diagnostics,
    }
    _governance_review_cache.update(
        {"key": cache_key, "expires_at": now + _GOVERNANCE_REVIEW_CACHE_TTL_SECONDS, "payload": payload}
    )
    return {**payload, **runtime}


@app.post("/memory/auto_hydration/policy")
def memory_auto_hydration_policy(request: AutoHydrationPolicyRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    policy = _auto_hydration_policy(request.agent_id)
    return {
        "ok": True,
        "policy": policy,
        **runtime,
    }


@app.post("/memory/governance/decision")
def memory_governance_decision(request: GovernanceDecisionRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    result = api.apply_governance_decision(
        request.reference,
        relationship=request.relationship,
        target_reference=request.target_reference,
        approved=request.approved,
    )
    return {
        "ok": result is not None,
        "reference": request.reference,
        "relationship": request.relationship,
        "target_reference": request.target_reference,
        "approved": request.approved,
        "result": result,
        **runtime,
    }


@app.post("/memory/governance/review/decision")
def memory_governance_review_decision(request: GovernanceReviewDecisionRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    result = api.apply_governance_review_decision(
        request.reference,
        target_reference=request.target_reference,
        approved=request.approved,
        kind=request.kind,
        relationship=request.relationship,
        context_depth=request.context_depth,
    )
    return {
        "ok": result is not None,
        "reference": request.reference,
        "target_reference": request.target_reference,
        "approved": request.approved,
        "kind": request.kind,
        "relationship": request.relationship,
        "context_depth": request.context_depth,
        "result": result,
        **runtime,
    }


@app.post("/memory/governance/summary")
def memory_governance_summary(request: GovernanceSummaryRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    summary = api.governance_summary(categories=request.categories)
    return {
        "ok": True,
        "categories": request.categories,
        "summary": summary,
        **runtime,
    }


@app.post("/memory/governance/queue")
def memory_governance_queue(request: GovernanceQueueRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    items = api.governance_queue(categories=request.categories, limit=request.limit)
    kind_counts: Dict[str, int] = {}
    bucket_counts: Dict[str, int] = {}
    priority_label_counts: Dict[str, int] = {}
    for item in items:
        item_kind = str(item.get("kind") or "unknown")
        item_bucket = str(item.get("bucket") or "unknown")
        priority_label = str(item.get("priority_label") or "unknown")
        kind_counts[item_kind] = kind_counts.get(item_kind, 0) + 1
        bucket_counts[item_bucket] = bucket_counts.get(item_bucket, 0) + 1
        priority_label_counts[priority_label] = priority_label_counts.get(priority_label, 0) + 1
    diagnostics = {
        "item_count": len(items),
        "kind_counts": kind_counts,
        "bucket_counts": bucket_counts,
        "priority_label_counts": priority_label_counts,
        "filters": {
            "categories": list(request.categories or []),
            "limit": int(request.limit),
        },
    }
    return {
        "ok": True,
        "categories": request.categories,
        "limit": request.limit,
        "items": items,
        "queueDiagnostics": diagnostics,
        **runtime,
    }


@app.post("/memory/governance/auto_resolve")
def memory_governance_auto_resolve(request: GovernanceAutoResolveRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    result = api.governance_auto_resolve(
        categories=request.categories,
        limit=request.limit,
        dry_run=request.dry_run,
        profile=request.profile,
    )
    actions = list(result.get("actions") or []) if isinstance(result, dict) else []
    reason_counts: Dict[str, int] = {}
    kind_counts: Dict[str, int] = {}
    applied_count = 0
    skipped_count = 0
    for action in actions:
        reason = str(action.get("reason") or "unknown")
        kind = str(action.get("kind") or "unknown")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        if bool(action.get("applied")):
            applied_count += 1
        else:
            skipped_count += 1
    diagnostics = {
        "action_count": len(actions),
        "applied_count": applied_count,
        "skipped_count": skipped_count,
        "reason_counts": reason_counts,
        "kind_counts": kind_counts,
        "policy_profile": ((result.get("policy") or {}).get("profile") if isinstance(result, dict) else None),
        "dry_run": bool(request.dry_run),
    }
    return {
        "ok": True,
        "categories": request.categories,
        "limit": request.limit,
        "dry_run": request.dry_run,
        "result": result,
        "autoResolveDiagnostics": diagnostics,
        **runtime,
    }


@app.post("/memory/governance/audit")
def memory_governance_audit(request: GovernanceAuditRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    items = api.governance_audit(limit=request.limit, kinds=request.kinds)
    event_counts: Dict[str, int] = {}
    status_counts: Dict[str, int] = {}
    for item in items:
        event_name = str(item.get("event") or "unknown")
        event_counts[event_name] = event_counts.get(event_name, 0) + 1
        status_value = str(item.get("status") or "unknown")
        status_counts[status_value] = status_counts.get(status_value, 0) + 1
    diagnostics = {
        "item_count": len(items),
        "event_counts": event_counts,
        "status_counts": status_counts,
        "filters": {
            "limit": int(request.limit),
            "kinds": list(request.kinds or []),
        },
    }
    return {
        "ok": True,
        "limit": request.limit,
        "kinds": request.kinds,
        "items": items,
        "auditDiagnostics": diagnostics,
        **runtime,
    }


@app.post("/memory/governance/rollback")
def memory_governance_rollback(request: GovernanceRollbackRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    result = api.rollback_governance_decision(
        request.reference,
        relationship=request.relationship,
        target_reference=request.target_reference,
    )
    diagnostics = {
        "requested_relationship": str(request.relationship or ""),
        "succeeded": result is not None,
        "result_kind": "rolled_back" if result is not None else "rollback_not_applied",
        "reference": request.reference,
        "target_reference": request.target_reference,
    }
    return {
        "ok": result is not None,
        "reference": request.reference,
        "relationship": request.relationship,
        "target_reference": request.target_reference,
        "result": result,
        "rollbackDiagnostics": diagnostics,
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
    if prefix in {*store.MEMORY_TABLES, "conversation_turns", "conversation_checkpoints"} and not identifier.isdigit():
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
    route_started = time.perf_counter()
    stage_marks: list[tuple[str, float]] = []

    def _mark(stage: str) -> None:
        stage_marks.append((stage, time.perf_counter()))

    cache_ttl_ms = _parse_float_env("OCMEMOG_HYDRATE_CACHE_TTL_MS", default=350.0, minimum=0.0)
    cache_key = (
        str(request.conversation_id or ""),
        str(request.session_id or ""),
        str(request.thread_id or ""),
        int(request.turns_limit),
        int(request.memory_limit),
    )
    if cache_ttl_ms > 0:
        with _HYDRATE_CACHE_LOCK:
            cached = _HYDRATE_CACHE.get(cache_key)
            now_ms = time.time() * 1000.0
            if cached and (now_ms - cached[0]) <= cache_ttl_ms:
                return {**cached[1], **runtime}
    turns = conversation_state.get_recent_turns(
        conversation_id=request.conversation_id,
        session_id=request.session_id,
        thread_id=request.thread_id,
        limit=request.turns_limit,
    )
    _mark("get_recent_turns")
    linked_memories = conversation_state.get_linked_memories(
        conversation_id=request.conversation_id,
        session_id=request.session_id,
        thread_id=request.thread_id,
        limit=request.memory_limit,
    )
    _mark("get_linked_memories")
    link_targets: List[Dict[str, Any]] = []
    if request.thread_id:
        link_targets.extend(memory_links.get_memory_links_for_thread(request.thread_id))
    if request.session_id:
        link_targets.extend(memory_links.get_memory_links_for_session(request.session_id))
    if request.conversation_id:
        link_targets.extend(memory_links.get_memory_links_for_conversation(request.conversation_id))
    conversation_state._self_heal_legacy_continuity_artifacts(
        conversation_id=request.conversation_id,
        session_id=request.session_id,
        thread_id=request.thread_id,
    )
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
    _mark("list_relevant_unresolved_state")
    summary = conversation_state.infer_hydration_payload(
        turns,
        conversation_id=request.conversation_id,
        session_id=request.session_id,
        thread_id=request.thread_id,
        unresolved_items=unresolved_items,
        latest_checkpoint=latest_checkpoint,
        linked_memories=linked_memories,
    )
    _mark("infer_hydration_payload")
    state_payload = conversation_state.get_state(
        conversation_id=request.conversation_id,
        session_id=request.session_id,
        thread_id=request.thread_id,
    )
    _mark("get_state")
    if not state_payload:
        state_payload = conversation_state._state_from_payload(
            summary,
            conversation_id=request.conversation_id,
            session_id=request.session_id,
            thread_id=request.thread_id,
        )
    state_meta = (state_payload or {}).get("metadata") if isinstance((state_payload or {}).get("metadata"), dict) else {}
    state_status = str(state_meta.get("state_status") or "")
    runtime["warnings"] = [*runtime["warnings"], "hydrate returned state without inline state refresh"]
    if state_status == "stale_persisted":
        runtime["warnings"] = [*runtime["warnings"], "hydrate returned persisted state while state refresh was delayed"]
    elif state_status == "derived_not_persisted":
        runtime["warnings"] = [*runtime["warnings"], "hydrate returned derived state without inline state refresh"]
    predictive_brief = _build_predictive_brief(
        request=request,
        turns=turns,
        summary=summary,
        linked_memories=linked_memories,
    )
    _mark("build_predictive_brief")
    response = {
        "ok": True,
        "conversation_id": request.conversation_id,
        "session_id": request.session_id,
        "thread_id": request.thread_id,
        "recent_turns": turns,
        "summary": summary,
        "predictive_brief": predictive_brief,
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
    elapsed_ms = round((time.perf_counter() - route_started) * 1000, 3)
    hydrate_trace_enabled = _parse_bool_env("OCMEMOG_TRACE_HYDRATE", default=False)
    hydrate_warn_ms_raw = os.environ.get("OCMEMOG_TRACE_HYDRATE_WARN_MS", "25").strip()
    try:
        hydrate_warn_ms = max(0.0, float(hydrate_warn_ms_raw))
    except Exception:
        hydrate_warn_ms = 25.0
    if cache_ttl_ms > 0:
        with _HYDRATE_CACHE_LOCK:
            _HYDRATE_CACHE[cache_key] = (time.time() * 1000.0, dict(response))
            if len(_HYDRATE_CACHE) > 256:
                oldest_key = min(_HYDRATE_CACHE.items(), key=lambda item: item[1][0])[0]
                _HYDRATE_CACHE.pop(oldest_key, None)
    if hydrate_trace_enabled or elapsed_ms >= hydrate_warn_ms:
        stage_details: list[str] = []
        previous = route_started
        for name, mark in stage_marks:
            stage_details.append(f"{name}={(mark - previous) * 1000.0:.3f}ms")
            previous = mark
        print(
            "[ocmemog][route] conversation_hydrate "
            f"elapsed_ms={elapsed_ms:.3f} turns={len(turns)} linked_memories={len(linked_memories)} "
            f"unresolved_items={len(unresolved_items)} state_status={state_status or 'fresh'} "
            f"stages={'|'.join(stage_details) or 'none'}",
            file=sys.stderr,
        )
    return response


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
    ingest_started = time.perf_counter()
    runtime = _runtime_payload()
    content = request.content.strip() if isinstance(request.content, str) else ""
    if not content:
        return {"ok": False, "error": "empty_content", **runtime}

    kind = (request.kind or "experience").lower()
    if kind == "memory":
        memory_type = (request.memory_type or "knowledge").lower()
        allowed = set(store.MEMORY_TABLES)
        if memory_type not in allowed:
            memory_type = "knowledge"
        memory_type = _retune_reflection_memory_type(content, memory_type)
        metadata = {
            **(request.metadata or {}),
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
            post_process=False,
        )
        reference = f"{memory_type}:{memory_id}"
        _enqueue_postprocess(reference, skip_embedding_provider=_parse_bool_env("OCMEMOG_POSTPROCESS_SKIP_EMBEDDING_PROVIDER", default=True))
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
        if request.role and _should_link_ingest_memory_to_turn(request):
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
        response = {"ok": True, "kind": "memory", "memory_type": memory_type, "reference": reference, "turn": turn_response, **runtime}
        elapsed_ms = round((time.perf_counter() - ingest_started) * 1000, 3)
        trace = _parse_bool_env("OCMEMOG_TRACE_INGEST_PIPELINE", default=False)
        warn_ms = _parse_float_env("OCMEMOG_TRACE_INGEST_PIPELINE_WARN_MS", default=20.0, minimum=0.0)
        if trace or elapsed_ms >= warn_ms:
            print(f"[ocmemog][ingest] ingest_request elapsed_ms={elapsed_ms:.3f} kind=memory source={request.source or ''} reference={reference}", file=sys.stderr)
        return response

    # experience ingest
    experience_metadata = provenance.normalize_metadata(
        {
            **(request.metadata or {}),
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
    started = time.perf_counter()
    payload = _ingest_request(request)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    print(f"[ocmemog][route] memory_ingest elapsed_ms={elapsed_ms:.3f} kind={request.kind} reference={payload.get('reference', '')}", file=sys.stderr)
    return payload


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
    payload = health.get_memory_health_fast()
    counts = payload.get("counts", {})
    counts["queue_depth"] = _queue_depth()
    counts["queue_processed"] = QUEUE_STATS.get("processed", 0)
    counts["queue_errors"] = QUEUE_STATS.get("errors", 0)
    payload["counts"] = counts

    coverage_tables = list(store.MEMORY_TABLES)
    conn = store.connect()
    try:
        vector_counts: Dict[str, int] = {str(row[0]): int(row[1] or 0) for row in conn.execute("SELECT source_type, COUNT(*) FROM vector_embeddings GROUP BY source_type")}
        payload["coverage"] = [
            {
                "table": table,
                "rows": int(counts.get(table, 0) or 0),
                "vectors": int(vector_counts.get(table, 0) or 0),
                "missing": max(int(counts.get(table, 0) or 0) - int(vector_counts.get(table, 0) or 0), 0),
            }
            for table in coverage_tables
        ]
    finally:
        conn.close()

    payload["queue"] = QUEUE_STATS
    return {"ok": True, "metrics": payload, **runtime}


def _event_stream():
    path = state_store.report_log_path()
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
    path = state_store.report_log_path()
    if not path.exists():
        return ""
    try:
        size = path.stat().st_size
        # Read only the trailing chunk to avoid loading very large logs.
        # This bounds dashboard latency even when the report log grows huge.
        max_bytes = 256 * 1024
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(-max_bytes, 2)
            data = handle.read()
        text = data.decode("utf-8", errors="ignore")
        lines = text.splitlines()
    except Exception as exc:
        print(f"[ocmemog][events] tail_read_failed path={path} error={exc!r}", file=sys.stderr)
        return ""
    return "\n".join(lines[-limit:])


@app.get("/dashboard")
def dashboard() -> HTMLResponse:
    metrics_payload = health.get_memory_health_fast()
    counts = metrics_payload.get("counts", {})
    coverage_tables = list(store.MEMORY_TABLES)
    conn = store.connect()
    try:
        cursor = conn.execute("SELECT source_type, COUNT(*) FROM vector_embeddings GROUP BY source_type")
        try:
            vector_rows = list(cursor)
        except TypeError:
            fetchall = getattr(cursor, "fetchall", None)
            if callable(fetchall):
                vector_rows = fetchall()
            else:
                fetchone = getattr(cursor, "fetchone", None)
                row = fetchone() if callable(fetchone) else None
                vector_rows = [row] if row is not None else []
        vector_counts: Dict[str, int] = {}
        for row in vector_rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            vector_counts[str(row[0])] = int(row[1] or 0)
        if hasattr(cursor, "close"):
            cursor.close()
        coverage_rows = []
        for table in coverage_tables:
            total = int(counts.get(table, 0) or 0)
            vectors = int(vector_counts.get(table, 0) or 0)
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
    local_html = "".join(
        f"<div class='card'><strong>{card['label']}</strong><br/>{card['value']}</div>"
        for card in metrics_cards
        if str(card.get('label', '')).startswith('local_') or str(card.get('label', '')) in {
            'frontier_calls_avoided_est',
            'prompt_tokens_saved_est',
            'completion_tokens_saved_est',
            'cost_saved_usd_est',
        }
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
        .panel {{ margin-top: 24px; }}
        .controls {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; margin: 8px 0; }}
        .controls label {{ display: flex; gap: 6px; align-items: center; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; vertical-align: top; }}
        th {{ background: #f7f7f7; }}
        button {{ padding: 6px 10px; }}
        .muted {{ color: #666; }}
        .error {{ color: #a40000; }}
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
      <div class="panel">
        <h3>Governance review</h3>
        <div class="controls">
          <label>Kind
            <select id="review-kind-filter">
              <option value="">All</option>
              <option value="duplicate_candidate">Duplicate</option>
              <option value="contradiction_candidate">Contradiction</option>
              <option value="supersession_recommendation">Supersession</option>
            </select>
          </label>
          <label>Priority
            <select id="review-priority-filter">
              <option value="">All</option>
              <option value="90">90</option>
              <option value="70">70</option>
              <option value="40">40</option>
            </select>
          </label>
          <button id="review-refresh" type="button">Refresh</button>
        </div>
        <div id="review-note" class="muted">Loading review items...</div>
        <div id="review-error" class="error"></div>
        <table>
          <thead>
            <tr>
              <th>Priority</th>
              <th>Review</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="review-table-body">
            <tr><td colspan="3" class="muted">Loading...</td></tr>
          </tbody>
        </table>
      </div>
      <h3>Ponder recommendations</h3>
      <div id="ponder-meta" style="margin-bottom:8px; color:#666;"></div>
      <div id="ponder"></div>
      <h3>Live events</h3>
      <pre id="events">{events_html}</pre>
      <script>
        const metricsEl = document.getElementById('metrics');
        const coverageEl = document.getElementById('coverage');
        const reviewKindFilterEl = document.getElementById('review-kind-filter');
        const reviewPriorityFilterEl = document.getElementById('review-priority-filter');
        const reviewRefreshEl = document.getElementById('review-refresh');
        const reviewNoteEl = document.getElementById('review-note');
        const reviewErrorEl = document.getElementById('review-error');
        const reviewTableBodyEl = document.getElementById('review-table-body');
        const ponderEl = document.getElementById('ponder');
        const ponderMetaEl = document.getElementById('ponder-meta');
        const eventsEl = document.getElementById('events');
        let reviewItems = [];
        let reviewLastRefresh = null;
        const pendingReviewIds = new Set();

        /**
         * @typedef {{Object}} GovernanceReviewContext
         * @property {{string}} reference
         * @property {{string | undefined}} bucket
         * @property {{number | undefined}} id
         * @property {{string | undefined}} timestamp
         * @property {{string | undefined}} content
         * @property {{string | undefined}} memory_status
         *
         * @typedef {{Object}} GovernanceReviewItem
         * @property {{string}} review_id
         * @property {{string}} kind
         * @property {{string | undefined}} kind_label
         * @property {{string | undefined}} relationship
         * @property {{number}} priority
         * @property {{string | undefined}} timestamp
         * @property {{number | undefined}} signal
         * @property {{string | undefined}} summary
         * @property {{string}} reference
         * @property {{string}} target_reference
         * @property {{GovernanceReviewContext | undefined}} source
         * @property {{GovernanceReviewContext | undefined}} target
         */

        function escapeHtml(value) {{
          return String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
        }}

        function formatTimestamp(value) {{
          if (!value) {{
            return 'n/a';
          }}
          const parsed = new Date(value);
          return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
        }}

        function summarizeReviewItem(item) {{
          const sourceRef = item.source?.reference || item.reference || 'source memory';
          const targetRef = item.target?.reference || item.target_reference || 'target memory';
          const sourceText = item.source?.content || sourceRef;
          const targetText = item.target?.content || targetRef;
          const relation = item.relationship || (item.kind_label || item.kind || 'relationship').toLowerCase();
          const when = item.timestamp ? ` Reviewed signal from ${{formatTimestamp(item.timestamp)}}.` : '';
          const signal = item.signal ? ` Signal score: ${{item.signal}}.` : '';
          return `${{sourceRef}} may ${{relation.replaceAll('_', ' ')}} ${{targetRef}}. Source: “${{sourceText}}” Target: “${{targetText}}”.${{signal}}${{when}}`;
        }}

        function renderReviewTable() {{
          const kindFilter = reviewKindFilterEl.value;
          const priorityFilter = reviewPriorityFilterEl.value;
          /** @type {{GovernanceReviewItem[]}} */
          const filtered = reviewItems.filter((item) => {{
            if (kindFilter && item.kind !== kindFilter) {{
              return false;
            }}
            if (priorityFilter && String(item.priority) !== priorityFilter) {{
              return false;
            }}
            return true;
          }});

          reviewNoteEl.textContent = `${{filtered.length}} items shown${{reviewItems.length !== filtered.length ? ` of ${{reviewItems.length}}` : ''}} • Last refresh: ${{reviewLastRefresh ? formatTimestamp(reviewLastRefresh) : 'n/a'}}`;

          if (!filtered.length) {{
            reviewTableBodyEl.innerHTML = '<tr><td colspan="3" class="muted">No review items match the current filters.</td></tr>';
            return;
          }}

          reviewTableBodyEl.innerHTML = filtered.map((item) => {{
            const disabled = pendingReviewIds.has(item.review_id) ? 'disabled' : '';
            const reviewText = summarizeReviewItem(item);
            const summaryBits = [item.kind_label || item.kind, item.summary].filter(Boolean).join(' • ');
            return `
              <tr>
                <td>${{escapeHtml(item.priority)}}</td>
                <td>
                  <strong>${{escapeHtml(summaryBits || 'Governance review item')}}</strong><br/>
                  <span class="muted">${{escapeHtml(reviewText)}}</span>
                </td>
                <td>
                  <button type="button" data-review-id="${{escapeHtml(item.review_id)}}" data-approved="true" ${{disabled}}>Approve</button>
                  <button type="button" data-review-id="${{escapeHtml(item.review_id)}}" data-approved="false" ${{disabled}}>Reject</button>
                </td>
              </tr>
            `;
          }}).join('');
        }}

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

        async function refreshGovernanceReview() {{
          reviewErrorEl.textContent = '';
          try {{
            const res = await fetch('/memory/governance/review/summary', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ limit: 20, context_depth: 0, scan_limit: 250 }}),
            }});
            const data = await res.json();
            if (!res.ok || !data.ok) {{
              throw new Error(data.error || `review request failed: ${{res.status}}`);
            }}
            reviewItems = Array.isArray(data.items) ? data.items : [];
            reviewLastRefresh = new Date().toISOString();
            renderReviewTable();
          }} catch (error) {{
            reviewErrorEl.textContent = error instanceof Error ? error.message : String(error);
            reviewTableBodyEl.innerHTML = '<tr><td colspan="3" class="muted">Unable to load review items.</td></tr>';
          }}
        }}

        async function applyGovernanceReviewDecision(reviewId, approved) {{
          const item = reviewItems.find((entry) => entry.review_id === reviewId);
          if (!item) {{
            return;
          }}
          pendingReviewIds.add(reviewId);
          renderReviewTable();
          reviewErrorEl.textContent = '';
          try {{
            const res = await fetch('/memory/governance/review/decision', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{
                reference: item.reference,
                target_reference: item.target_reference,
                approved,
                kind: item.kind,
                relationship: item.relationship,
                context_depth: 1,
              }}),
            }});
            const data = await res.json();
            if (!res.ok || !data.ok) {{
              throw new Error(data.error || `decision request failed: ${{res.status}}`);
            }}
            await refreshGovernanceReview();
          }} catch (error) {{
            reviewErrorEl.textContent = error instanceof Error ? error.message : String(error);
          }} finally {{
            pendingReviewIds.delete(reviewId);
            renderReviewTable();
          }}
        }}

        reviewKindFilterEl.addEventListener('change', renderReviewTable);
        reviewPriorityFilterEl.addEventListener('change', renderReviewTable);
        reviewRefreshEl.addEventListener('click', refreshGovernanceReview);
        reviewTableBodyEl.addEventListener('click', (event) => {{
          const target = event.target;
          if (!(target instanceof HTMLButtonElement)) {{
            return;
          }}
          const reviewId = target.dataset.reviewId;
          if (!reviewId) {{
            return;
          }}
          applyGovernanceReviewDecision(reviewId, target.dataset.approved === 'true');
        }});

        refreshMetrics();
        refreshGovernanceReview();
        refreshPonder();
        setInterval(refreshMetrics, 5000);
        setInterval(refreshGovernanceReview, 15000);
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
