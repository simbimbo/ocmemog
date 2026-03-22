"""LLM inference helpers owned by ocmemog."""

from __future__ import annotations

import json
import os
import re
import urllib.request

from . import config, instrumentation, state_store


LOGFILE = state_store.report_log_path()


def _infer_openai_compatible(
    prompt: str,
    *,
    base_url: str,
    model: str,
    api_key: str | None = None,
    provider_label: str = "openai-compatible",
) -> dict[str, str]:
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        instrumentation.emit_event(LOGFILE, "brain_infer_error", status="error", provider=provider_label, error=str(exc))
        return {"status": "error", "error": f"request_failed:{exc}"}

    try:
        output = response["choices"][0]["message"]["content"]
    except Exception as exc:
        instrumentation.emit_event(LOGFILE, "brain_infer_error", status="error", provider=provider_label, error=str(exc))
        return {"status": "error", "error": "invalid_response"}

    return {"status": "ok", "output": str(output).strip()}


def _infer_ollama(prompt: str, model: str | None = None) -> dict[str, str]:
    payload = {
        "model": model or config.OCMEMOG_OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{config.OCMEMOG_OLLAMA_HOST.rstrip('/')}/api/generate", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        instrumentation.emit_event(LOGFILE, "brain_infer_error", status="error", provider="ollama", error=str(exc))
        return {"status": "error", "error": f"ollama_failed:{exc}"}
    output = response.get("response")
    if not output:
        instrumentation.emit_event(LOGFILE, "brain_infer_error", status="error", provider="ollama", error="invalid_response")
        return {"status": "error", "error": "invalid_response"}
    return {"status": "ok", "output": str(output).strip()}


def _looks_like_local_openai_model(name: str) -> bool:
    if not name:
        return False
    lowered = name.strip().lower()
    return lowered.startswith("local-openai:") or lowered.startswith("local_openai:") or lowered.startswith("llamacpp:")


def _normalize_local_model_name(name: str) -> str:
    lowered = (name or "").strip()
    for prefix in ("local-openai:", "local_openai:", "llamacpp:"):
        if lowered.lower().startswith(prefix):
            return lowered[len(prefix):]
    return lowered


def _looks_like_ollama_model(name: str) -> bool:
    if not name:
        return False
    lowered = name.strip().lower()
    if lowered.startswith("ollama:"):
        return True
    if "/" in lowered:
        return False
    return ":" in lowered


def stats() -> dict[str, object]:
    materialized_local = int(_LOCAL_INFER_STATS.get("local_success", 0)) + int(_LOCAL_INFER_STATS.get("cache_hits", 0))
    est_prompt_tokens_saved = materialized_local * _AVG_PROMPT_TOKENS_SAVED
    est_completion_tokens_saved = materialized_local * _AVG_COMPLETION_TOKENS_SAVED
    est_cost_saved = (
        (est_prompt_tokens_saved / 1000.0) * _EST_FRONTIER_INPUT_COST_PER_1K
        + (est_completion_tokens_saved / 1000.0) * _EST_FRONTIER_OUTPUT_COST_PER_1K
    )
    return {
        "cache_entries": len(_LOCAL_INFER_CACHE),
        "warm_models": sorted(_MODEL_WARM_STATE.keys()),
        "frontier_calls_avoided_est": materialized_local,
        "prompt_tokens_saved_est": est_prompt_tokens_saved,
        "completion_tokens_saved_est": est_completion_tokens_saved,
        "cost_saved_usd_est": round(est_cost_saved, 4),
        **{k: int(v) for k, v in _LOCAL_INFER_STATS.items()},
    }


def infer(prompt: str, provider_name: str | None = None) -> dict[str, str]:
    if not isinstance(prompt, str) or not prompt.strip():
        return {"status": "error", "error": "empty_prompt"}

    use_ollama = os.environ.get("OCMEMOG_USE_OLLAMA", "").lower() in {"1", "true", "yes"}
    model_override = provider_name or config.OCMEMOG_MEMORY_MODEL
    if _looks_like_local_openai_model(model_override):
        model = _normalize_local_model_name(model_override) or config.OCMEMOG_LOCAL_LLM_MODEL
        return _infer_openai_compatible(
            prompt,
            base_url=config.OCMEMOG_LOCAL_LLM_BASE_URL,
            model=model,
            api_key=os.environ.get("OCMEMOG_LOCAL_LLM_API_KEY") or os.environ.get("LOCAL_LLM_API_KEY"),
            provider_label="local-openai",
        )
    if use_ollama or _looks_like_ollama_model(model_override):
        model = model_override.split(":", 1)[-1] if model_override.startswith("ollama:") else model_override
        return _infer_ollama(prompt, model)

    api_key = os.environ.get("OCMEMOG_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return _infer_openai_compatible(
            prompt,
            base_url=config.OCMEMOG_LOCAL_LLM_BASE_URL,
            model=config.OCMEMOG_LOCAL_LLM_MODEL,
            api_key=os.environ.get("OCMEMOG_LOCAL_LLM_API_KEY") or os.environ.get("LOCAL_LLM_API_KEY"),
            provider_label="local-openai",
        )

    model = model_override
    return _infer_openai_compatible(
        prompt,
        base_url=config.OCMEMOG_OPENAI_API_BASE,
        model=model,
        api_key=api_key,
        provider_label="openai",
    )


def parse_operator_name(text: str) -> dict[str, str] | None:
    match = re.search(r"\bmy name is ([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    return {"name": match.group(1)}

