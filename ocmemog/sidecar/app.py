from __future__ import annotations

from typing import Any, Iterable

from fastapi import FastAPI
from pydantic import BaseModel, Field

from brain.runtime.memory import retrieval, store
from ocmemog.sidecar.compat import flatten_results, probe_runtime

DEFAULT_CATEGORIES = ("knowledge", "reflections", "directives", "tasks")

app = FastAPI(title="ocmemog sidecar", version="0.0.1")


class SearchRequest(BaseModel):
    query: str = Field(default="")
    limit: int = Field(default=5, ge=1, le=50)
    categories: list[str] | None = None


class GetRequest(BaseModel):
    reference: str


def _normalize_categories(categories: Iterable[str] | None) -> list[str]:
    selected = [item for item in (categories or DEFAULT_CATEGORIES) if item in DEFAULT_CATEGORIES]
    return selected or list(DEFAULT_CATEGORIES)


def _runtime_payload() -> dict[str, Any]:
    status = probe_runtime()
    return {
        "mode": status.mode,
        "missingDeps": status.missing_deps,
        "todo": status.todo,
        "warnings": status.warnings,
    }


def _fallback_search(query: str, limit: int, categories: list[str]) -> list[dict[str, Any]]:
    conn = store.connect()
    try:
        results: list[dict[str, Any]] = []
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


def _get_row(reference: str) -> dict[str, Any] | None:
    table, sep, raw_id = reference.partition(":")
    if not sep or not table or not raw_id.isdigit():
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
