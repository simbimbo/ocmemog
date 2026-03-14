from __future__ import annotations

import os
import threading
from typing import Any, Dict, Iterable, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from brain.runtime.memory import retrieval, store, api, distill
from ocmemog.sidecar.compat import flatten_results, probe_runtime
from ocmemog.sidecar.transcript_watcher import watch_forever

DEFAULT_CATEGORIES = ("knowledge", "reflections", "directives", "tasks", "runbooks", "lessons")

app = FastAPI(title="ocmemog sidecar", version="0.0.1")


@app.on_event("startup")
def _start_transcript_watcher() -> None:
    enabled = os.environ.get("OCMEMOG_TRANSCRIPT_WATCHER", "").lower() in {"1", "true", "yes"}
    if not enabled:
        return
    thread = threading.Thread(target=watch_forever, daemon=True)
    thread.start()


class SearchRequest(BaseModel):
    query: str = Field(default="")
    limit: int = Field(default=5, ge=1, le=50)
    categories: Optional[List[str]] = None


class GetRequest(BaseModel):
    reference: str


class IngestRequest(BaseModel):
    content: str
    kind: str = Field(default="experience", description="experience or memory")
    memory_type: Optional[str] = Field(default=None, description="knowledge|reflections|directives|tasks|runbooks|lessons")
    source: Optional[str] = None
    task_id: Optional[str] = None


class DistillRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=100)


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


@app.post("/memory/ingest")
def memory_ingest(request: IngestRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    content = request.content.strip() if isinstance(request.content, str) else ""
    if not content:
        return {"ok": False, "error": "empty_content", **runtime}

    kind = (request.kind or "experience").lower()
    if kind == "memory":
        api.store_memory(request.memory_type or "knowledge", content, source=request.source)
        return {"ok": True, "kind": "memory", "memory_type": request.memory_type or "knowledge", **runtime}

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


@app.post("/memory/distill")
def memory_distill(request: DistillRequest) -> dict[str, Any]:
    runtime = _runtime_payload()
    results = distill.distill_experiences(limit=request.limit)
    return {"ok": True, "count": len(results), "results": results, **runtime}
