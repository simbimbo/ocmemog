from __future__ import annotations

import importlib
import importlib.util
import os
from dataclasses import dataclass
from typing import Any

from ocmemog.runtime import identity


@dataclass(frozen=True)
class RuntimeStatus:
    mode: str
    missing_deps: list[str]
    todo: list[str]
    warnings: list[str]
    identity: dict[str, Any]
    capabilities: list[dict[str, Any]]
    runtime_summary: dict[str, Any]


TODO_ITEMS = [
    "Add non-OpenAI embedding providers if required.",
]

_EMBEDDING_PROVIDER_BACKEND_HINTS = {
    "openai",
    "openai_compatible",
    "openai-compatible",
    "local-openai",
    "local_openai",
    "llamacpp",
    "llama.cpp",
    "ollama",
    "local-ollama",
}


def probe_runtime() -> RuntimeStatus:
    runtime_identity = identity.get_runtime_identity()
    capabilities = runtime_identity.get("capabilities", [])

    missing_deps: list[str] = []
    warnings: list[str] = []

    for module_name in (
        "ocmemog.runtime.memory.store",
        "ocmemog.runtime.memory.retrieval",
        "ocmemog.runtime.memory.vector_index",
        "ocmemog.runtime.memory.memory_links",
    ):
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            missing_deps.append(f"{module_name}: {exc}")

    provider = (
        os.environ.get("OCMEMOG_EMBED_MODEL_PROVIDER")
        or os.environ.get("OCMEMOG_EMBED_PROVIDER", "")
        or os.environ.get("BRAIN_EMBED_MODEL_PROVIDER", "")
    ).strip().lower()
    if importlib.util.find_spec("sentence_transformers") is None and provider not in _EMBEDDING_PROVIDER_BACKEND_HINTS:
        warnings.append("Optional dependency missing: sentence-transformers; using local hash embeddings.")

    try:
        from ocmemog.runtime import inference, providers

        if getattr(inference, "__shim__", False):
            missing_deps.append("ocmemog.runtime.inference (shim only)")
        if getattr(getattr(providers, "provider_execute", None), "__shim__", False):
            missing_deps.append("ocmemog.runtime.providers.provider_execute (shim only)")
    except Exception as exc:
        missing_deps.append(f"ocmemog.runtime compatibility probe failed: {exc}")

    shim_count = sum(1 for item in capabilities if item.get("owner") == "brain-runtime-shim")
    if shim_count:
        warnings.append(f"Runtime still relies on {shim_count} legacy compatibility surface(s).")
        mode = "degraded"
    else:
        mode = "ready"

    if missing_deps:
        mode = "degraded"

    runtime_summary = {
        "mode": mode,
        "embedding_provider": provider or "local-simple",
        "using_hash_embeddings": bool(importlib.util.find_spec("sentence_transformers") is None and provider not in _EMBEDDING_PROVIDER_BACKEND_HINTS),
        "shim_surface_count": shim_count,
        "missing_dep_count": len(missing_deps),
        "warning_count": len(warnings),
    }
    return RuntimeStatus(
        mode=mode,
        missing_deps=missing_deps,
        todo=list(TODO_ITEMS),
        warnings=warnings,
        identity=runtime_identity,
        capabilities=capabilities,
        runtime_summary=runtime_summary,
    )


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
