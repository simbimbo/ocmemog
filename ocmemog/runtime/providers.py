"""Embedding provider execution wrapper owned by ocmemog."""

from __future__ import annotations

import json
import os
import urllib.request

from . import config, instrumentation, state_store

__wrapped_from__ = "brain.runtime.providers"

LOGFILE = state_store.report_log_path()


class ProviderExecute:
    def execute_embedding_call(self, selection, text: str) -> dict[str, object]:
        provider_id = getattr(selection, "provider_id", "") or ""
        model = getattr(selection, "model", "") or config.OCMEMOG_OPENAI_EMBED_MODEL
        if provider_id in {"openai", "local-openai"}:
            api_key = None
            url_base = config.OCMEMOG_OPENAI_API_BASE
            provider_label = "openai"
            if provider_id == "openai":
                api_key = os.environ.get("OCMEMOG_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
                if not api_key:
                    return {}
            else:
                url_base = config.OCMEMOG_LOCAL_EMBED_BASE_URL
                api_key = os.environ.get("OCMEMOG_LOCAL_EMBED_API_KEY") or os.environ.get("LOCAL_EMBED_API_KEY")
                provider_label = "local-openai"
            url = f"{url_base.rstrip('/')}/embeddings"
            payload = json.dumps({"model": model, "input": text}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, method="POST")
            if api_key:
                req.add_header("Authorization", f"Bearer {api_key}")
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                instrumentation.emit_event(
                    LOGFILE, "brain_embedding_provider_error", status="error", provider=provider_label, error=str(exc)
                )
                return {}
            try:
                embedding = data["data"][0]["embedding"]
            except Exception as exc:
                instrumentation.emit_event(
                    LOGFILE, "brain_embedding_provider_error", status="error", provider=provider_label, error=str(exc)
                )
                return {}
            return {"embedding": embedding}

        if provider_id == "ollama":
            url = f"{config.OCMEMOG_OLLAMA_HOST.rstrip('/')}/api/embeddings"
            payload = json.dumps({"model": model, "prompt": text}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, method="POST")
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                instrumentation.emit_event(LOGFILE, "brain_embedding_provider_error", status="error", provider="ollama", error=str(exc))
                return {}
            embedding = data.get("embedding")
            if not isinstance(embedding, list):
                instrumentation.emit_event(
                    LOGFILE, "brain_embedding_provider_error", status="error", provider="ollama", error="invalid_embedding"
                )
                return {}
            return {"embedding": embedding}

        return {}


provider_execute = ProviderExecute()
provider_execute.__shim__ = False

