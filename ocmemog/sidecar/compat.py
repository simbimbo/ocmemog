from __future__ import annotations

import importlib
import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ocmemog.runtime import config, identity, state_store


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


def _parse_agent_id_list(raw: str | None) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _queue_runtime_summary() -> dict[str, Any]:
    queue_path = state_store.data_dir() / "ingest_queue.jsonl"
    stats_path = state_store.data_dir() / "queue_stats.json"
    depth = 0
    invalid_lines = 0
    retrying_lines = 0
    max_retry_seen = 0
    try:
        if queue_path.exists():
            import json

            with queue_path.open("r", encoding="utf-8", errors="ignore") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    depth += 1
                    try:
                        payload = json.loads(line)
                        if isinstance(payload, dict):
                            retry_count = int(payload.get("_ocmemog_retry_count", 0) or 0)
                            if retry_count > 0:
                                retrying_lines += 1
                                max_retry_seen = max(max_retry_seen, retry_count)
                    except Exception:
                        invalid_lines += 1
    except Exception:
        depth = 0
        invalid_lines = 0
        retrying_lines = 0
        max_retry_seen = 0

    stats: dict[str, Any] = {}
    try:
        if stats_path.exists():
            import json

            parsed = json.loads(stats_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                stats = parsed
    except Exception:
        stats = {}

    worker_enabled = str(os.environ.get("OCMEMOG_INGEST_ASYNC_WORKER", "false")).strip().lower() in {"1", "true", "yes"}
    error_count = int(stats.get("errors") or 0)
    hints: list[str] = []
    severity = "ok"
    backlog_severity = "low"
    if depth > 0 and not worker_enabled:
        severity = "warn"
        hints.append("queue has backlog but async worker is disabled")
    if error_count > 0:
        severity = "warn"
        hints.append("queue has recorded ingest/parse errors")
    if invalid_lines > 0:
        severity = "warn"
        hints.append("queue contains invalid lines")
    if retrying_lines > 0:
        severity = "warn"
        hints.append("queue contains retrying payloads")
    if depth > 1000:
        backlog_severity = "critical"
    elif depth > 100:
        backlog_severity = "high"
    elif depth > 25:
        backlog_severity = "medium"
    if depth > 100:
        severity = "high"
        hints.append("queue backlog is high")
    elif depth > 25 and severity == "ok":
        severity = "warn"
        hints.append("queue backlog is elevated")

    return {
        "depth": int(depth),
        "queue_depth": int(depth),
        "queue_backlog_severity": backlog_severity,
        "last_run": stats.get("last_run"),
        "last_batch": int(stats.get("last_batch") or 0),
        "processed_total": int(stats.get("processed") or 0),
        "error_count": error_count,
        "last_error": stats.get("last_error"),
        "invalid_lines": int(invalid_lines),
        "retrying_lines": int(retrying_lines),
        "max_retry_seen": int(max_retry_seen),
        "worker_enabled": worker_enabled,
        "severity": severity,
        "hints": hints,
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
    local_model = str(
        getattr(config, "OCMEMOG_EMBED_MODEL_LOCAL", "")
        or getattr(config, "BRAIN_EMBED_MODEL_LOCAL", getattr(config, "OCMEMOG_EMBED_LOCAL_MODEL", "simple"))
        or ""
    ).strip().lower()
    sentence_transformers_ready = importlib.util.find_spec("sentence_transformers") is not None
    local_simple_only = local_model in {"", "simple", "hash"}
    provider_configured = provider in _EMBEDDING_PROVIDER_BACKEND_HINTS
    using_hash_embeddings = bool(not provider_configured and local_model in {"", "simple", "hash"} and not sentence_transformers_ready)
    if not sentence_transformers_ready and provider not in _EMBEDDING_PROVIDER_BACKEND_HINTS:
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

    hydration_allow_agents = _parse_agent_id_list(os.environ.get("OCMEMOG_AUTO_HYDRATION_ALLOW_AGENT_IDS"))
    hydration_deny_agents = _parse_agent_id_list(os.environ.get("OCMEMOG_AUTO_HYDRATION_DENY_AGENT_IDS"))
    runtime_summary = {
        "mode": mode,
        "embedding_provider": provider or "local-simple",
        "embedding_local_model": local_model or "simple",
        "embedding_path_summary": {
            "provider_configured": provider_configured,
            "provider_backend_hint": provider if provider else None,
            "local_model": local_model or "simple",
            "local_simple_only": local_simple_only,
            "sentence_transformers_ready": sentence_transformers_ready,
        },
        "using_hash_embeddings": using_hash_embeddings,
        "shim_surface_count": shim_count,
        "missing_dep_count": len(missing_deps),
        "warning_count": len(warnings),
        "queue": _queue_runtime_summary(),
        "auto_hydration": {
            "enabled": str(os.environ.get("OCMEMOG_AUTO_HYDRATION", "false")).strip().lower() in {"1", "true", "yes"},
            "allow_agent_ids": hydration_allow_agents,
            "deny_agent_ids": hydration_deny_agents,
            "scoped_by_agent": bool(hydration_allow_agents or hydration_deny_agents),
        },
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
