from __future__ import annotations

import importlib
import importlib.util
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeStatus:
    mode: str
    missing_deps: list[str]
    todo: list[str]
    warnings: list[str]


TODO_ITEMS = [
    "Add a role registry (brain.runtime.roles) if you want role-prioritized context building.",
    "Add non-OpenAI embedding providers if required.",
]


def probe_runtime() -> RuntimeStatus:
    missing_deps: list[str] = []
    warnings: list[str] = []

    for module_name in (
        "brain.runtime.memory.store",
        "brain.runtime.memory.retrieval",
        "brain.runtime.memory.vector_index",
        "brain.runtime.memory.memory_links",
    ):
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            missing_deps.append(f"{module_name}: {exc}")

    provider = os.environ.get("BRAIN_EMBED_MODEL_PROVIDER", "").strip().lower()
    if importlib.util.find_spec("sentence_transformers") is None and provider not in {"ollama", "openai", "openai_compatible", "openai-compatible", "local-ollama"}:
        warnings.append("Optional dependency missing: sentence-transformers; using local hash embeddings.")

    try:
        from brain.runtime import inference, providers

        if getattr(inference, "__shim__", False):
            missing_deps.append("brain.runtime.inference (shim only)")
        if getattr(getattr(providers, "provider_execute", None), "__shim__", False):
            missing_deps.append("brain.runtime.providers.provider_execute (shim only)")
    except Exception as exc:
        missing_deps.append(f"brain.runtime compatibility probe failed: {exc}")

    mode = "degraded" if missing_deps else "ready"
    return RuntimeStatus(mode=mode, missing_deps=missing_deps, todo=list(TODO_ITEMS), warnings=warnings)


def flatten_results(results: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for bucket, entries in results.items():
        for entry in entries:
            reference = str(entry.get("memory_reference") or "")
            table, _, raw_id = reference.partition(":")
            flattened.append(
                {
                    "bucket": bucket,
                    "reference": reference,
                    "memory_reference": reference,
                    "table": table or bucket,
                    "id": raw_id,
                    "content": entry.get("content", ""),
                    "score": float(entry.get("score", 0.0) or 0.0),
                    "links": entry.get("links", []),
                    "provenance": entry.get("provenance_preview") or {},
                    "retrieval_signals": entry.get("retrieval_signals") or {},
                    "selected_because": entry.get("selected_because"),
                    "timestamp": entry.get("timestamp"),
                }
            )
    flattened.sort(key=lambda item: item["score"], reverse=True)
    return flattened
