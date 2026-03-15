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

from brain.runtime import state_store
from brain.runtime.memory import retrieval, store, api, distill, health, memory_links, pondering_engine, reinforcement
from ocmemog.sidecar.compat import flatten_results, probe_runtime
from ocmemog.sidecar.transcript_watcher import watch_forever

DEFAULT_CATEGORIES = ("knowledge", "reflections", "directives", "tasks", "runbooks", "lessons")

app = FastAPI(title="ocmemog sidecar", version="0.0.1")

API_TOKEN = os.environ.get("OCMEMOG_API_TOKEN")

QUEUE_LOCK = threading.Lock()
QUEUE_STATS = {
    "last_run": None,
    "processed": 0,
    "errors": 0,
    "last_error": None,
    "last_batch": 0,
}


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
        return sum(1 for _ in path.open("r", encoding="utf-8", errors="ignore"))
    except Exception:
        return 0


def _enqueue_payload(payload: Dict[str, Any]) -> int:
    path = _queue_path()
    line = json.dumps(payload, ensure_ascii=False)
    with QUEUE_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    return _queue_depth()


def _ingest_worker() -> None:
    enabled = os.environ.get("OCMEMOG_INGEST_ASYNC_WORKER", "true").lower() in {"1", "true", "yes"}
    if not enabled:
        return
    poll_seconds = float(os.environ.get("OCMEMOG_INGEST_ASYNC_POLL_SECONDS", "5"))
    batch_max = int(os.environ.get("OCMEMOG_INGEST_ASYNC_BATCH_MAX", "25"))
    path = _queue_path()

    while True:
        batch: List[Dict[str, Any]] = []
        remaining: List[str] = []
        with QUEUE_LOCK:
            try:
                with path.open("r", encoding="utf-8", errors="ignore") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        if len(batch) < batch_max:
                            try:
                                batch.append(json.loads(line))
                            except Exception:
                                continue
                        else:
                            remaining.append(line)
                with path.open("w", encoding="utf-8") as handle:
                    for line in remaining:
                        handle.write(line + "\n")
            except Exception:
                batch = []

        if batch:
            QUEUE_STATS["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
            QUEUE_STATS["last_batch"] = len(batch)
            for payload in batch:
                try:
                    req = IngestRequest(**payload)
                    _ingest_request(req)
                    QUEUE_STATS["processed"] += 1
                except Exception as exc:
                    QUEUE_STATS["errors"] += 1
                    QUEUE_STATS["last_error"] = str(exc)
        time.sleep(poll_seconds)


def _drain_queue(limit: Optional[int] = None) -> Dict[str, Any]:
    path = _queue_path()
    processed = 0
    errors = 0
    last_error = None

    while True:
        batch: List[Dict[str, Any]] = []
        remaining: List[str] = []
        with QUEUE_LOCK:
            try:
                with path.open("r", encoding="utf-8", errors="ignore") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        if limit is not None and processed + len(batch) >= limit:
                            remaining.append(line)
                            continue
                        try:
                            batch.append(json.loads(line))
                        except Exception:
                            continue
                with path.open("w", encoding="utf-8") as handle:
                    for line in remaining:
                        handle.write(line + "\n")
            except Exception as exc:
                last_error = str(exc)
                break

        if not batch:
            break

        for payload in batch:
            try:
                req = IngestRequest(**payload)
                _ingest_request(req)
                processed += 1
            except Exception as exc:
                errors += 1
                last_error = str(exc)

        if limit is not None and processed >= limit:
            break

    QUEUE_STATS["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
    QUEUE_STATS["last_batch"] = processed
    if errors:
        QUEUE_STATS["errors"] += errors
        QUEUE_STATS["last_error"] = last_error
    QUEUE_STATS["processed"] += processed
    return {"processed": processed, "errors": errors, "last_error": last_error}


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


class PonderRequest(BaseModel):
    max_items: int = Field(default=5, ge=1, le=50)


class IngestRequest(BaseModel):
    content: str
    kind: str = Field(default="experience", description="experience or memory")
    memory_type: Optional[str] = Field(default=None, description="knowledge|reflections|directives|tasks|runbooks|lessons")
    source: Optional[str] = None
    task_id: Optional[str] = None
    session_id: Optional[str] = None
    thread_id: Optional[str] = None
    message_id: Optional[str] = None
    transcript_path: Optional[str] = None
    transcript_offset: Optional[int] = None
    timestamp: Optional[str] = None


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


def _get_row(reference: str) -> Optional[Dict[str, Any]]:
    table, sep, raw_id = reference.partition(":")
    if not sep or not table or not raw_id.isdigit():
        return None

    allowed_tables = {
        "knowledge",
        "reflections",
        "directives",
        "tasks",
        "runbooks",
        "lessons",
        "candidates",
        "promotions",
    }
    if table not in allowed_tables:
        return None

    conn = store.connect()
    try:
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (int(raw_id),)).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["reference"] = reference
        payload["table"] = table
        payload["id"] = int(raw_id)
        return payload
    finally:
        conn.close()


def _parse_transcript_target(target: str) -> tuple[Path, Optional[int]] | None:
    if not target.startswith("transcript:"):
        return None
    raw = target[len("transcript:"):]
    if "#L" in raw:
        path_str, line_str = raw.split("#L", 1)
        try:
            line_no = int(line_str)
        except Exception:
            line_no = None
    else:
        path_str = raw
        line_no = None
    path = Path(path_str).expanduser()
    return (path, line_no)


def _allowed_transcript_roots() -> list[Path]:
    raw = os.environ.get("OCMEMOG_TRANSCRIPT_ROOTS")
    if raw:
        roots = [Path(item).expanduser().resolve() for item in raw.split(",") if item.strip()]
    else:
        roots = [Path.home() / ".openclaw" / "workspace" / "memory"]
    return roots


def _read_transcript_snippet(path: Path, line_no: Optional[int], radius: int) -> Dict[str, Any]:
    path = path.expanduser().resolve()
    allowed = _allowed_transcript_roots()
    if not any(path.is_relative_to(root) for root in allowed):
        return {"ok": False, "error": "transcript_path_not_allowed", "path": str(path)}
    if not path.exists() or not path.is_file():
        return {"ok": False, "error": "missing_transcript", "path": str(path)}

    start = 0 if line_no is None or line_no <= 0 else max(0, line_no - radius)
    end = start + radius * 2 if line_no else radius

    snippet_lines = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for idx, line in enumerate(handle, start=1):
            if idx < start + 1:
                continue
            if idx > end:
                break
            snippet_lines.append(line.rstrip("\n"))

    if not snippet_lines:
        return {"ok": False, "error": "empty_transcript", "path": str(path)}

    return {
        "ok": True,
        "path": str(path),
        "start_line": start + 1,
        "end_line": start + len(snippet_lines),
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
    row = _get_row(request.reference)
    if row is None:
        return {
            "ok": False,
            "error": "TODO: memory reference was not found or is not yet supported by the sidecar.",
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
            path, line_no = parsed
            transcript = _read_transcript_snippet(path, line_no, request.radius)
            break
    return {
        "ok": True,
        "reference": request.reference,
        "links": links,
        "transcript": transcript,
        **runtime,
    }


@app.post("/memory/ponder")
def memory_ponder(request: PonderRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    results = pondering_engine.run_ponder_cycle(max_items=request.max_items)
    insights = results.get("insights", []) or []
    for item in insights:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary")
        recommendation = item.get("recommendation")
        if summary:
            api.store_memory(
                "reflections",
                str(summary),
                source="ponder",
                metadata={
                    "kind": "ponder",
                    "recommendation": recommendation,
                    "reference": item.get("reference"),
                },
            )
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
        items.append({
            "reference": f"reflections:{row['id']}",
            "timestamp": row["timestamp"],
            "summary": row["content"],
            "recommendation": meta.get("recommendation"),
            "source_reference": meta.get("reference"),
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
            "session_id": request.session_id,
            "thread_id": request.thread_id,
            "message_id": request.message_id,
            "transcript_path": request.transcript_path,
            "transcript_offset": request.transcript_offset,
        }
        memory_id = api.store_memory(
            memory_type,
            content,
            source=request.source,
            metadata=metadata,
            timestamp=request.timestamp,
        )
        reference = f"{memory_type}:{memory_id}"
        if request.session_id:
            memory_links.add_memory_link(reference, "session", f"session:{request.session_id}")
        if request.thread_id:
            memory_links.add_memory_link(reference, "thread", f"thread:{request.thread_id}")
        if request.message_id:
            memory_links.add_memory_link(reference, "message", f"message:{request.message_id}")
        if request.transcript_path:
            suffix = f"#L{request.transcript_offset}" if request.transcript_offset else ""
            memory_links.add_memory_link(reference, "transcript", f"transcript:{request.transcript_path}{suffix}")
        return {"ok": True, "kind": "memory", "memory_type": memory_type, "reference": reference, **runtime}

    # experience ingest
    conn = store.connect()
    conn.execute(
        "INSERT INTO experiences (task_id, outcome, reward_score, confidence, experience_type, source_module, schema_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            request.task_id,
            content,
            None,
            1.0,
            "ingest",
            request.source or "sidecar",
            store.SCHEMA_VERSION,
        ),
    )
    conn.commit()
    conn.close()
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
    metrics_html = "".join(
        f"<div class='card'><strong>{key}</strong><br/>{value}</div>" for key, value in counts.items()
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
      <h3>Ponder recommendations</h3>
      <div id="ponder-meta" style="margin-bottom:8px; color:#666;"></div>
      <div id="ponder"></div>
      <h3>Live events</h3>
      <pre id="events">{events_html}</pre>
      <script>
        const metricsEl = document.getElementById('metrics');
        const ponderEl = document.getElementById('ponder');
        const ponderMetaEl = document.getElementById('ponder-meta');
        const eventsEl = document.getElementById('events');

        async function refreshMetrics() {{
          const res = await fetch('/metrics');
          const data = await res.json();
          const counts = data.metrics?.counts || {{}};
          metricsEl.innerHTML = Object.entries(counts).map(([k,v]) =>
            `<div class=\"card\"><strong>${{k}}</strong><br/>${{v}}</div>`
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
            `<div class=\"card\"><strong>${{item.summary}}</strong><br/><em>${{item.recommendation || ''}}</em><br/><small>${{item.timestamp || ''}} • ${{item.reference || ''}}</small></div>`
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
