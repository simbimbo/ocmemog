from __future__ import annotations

import hashlib
from typing import List, Any

from brain.runtime import config, state_store, model_router
from brain.runtime.instrumentation import emit_event
from brain.runtime.providers import provider_execute

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"
_MODEL_CACHE: dict[str, Any] = {}


def _simple_embedding(text: str, dims: int = 8) -> List[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = [digest[i] / 255.0 for i in range(dims)]
    return values


def _load_sentence_transformer(model_name: str) -> Any | None:
    if model_name in _MODEL_CACHE:
        return _MODEL_CACHE[model_name]
    try:
        from sentence_transformers import SentenceTransformer
    except Exception:
        return None
    model = SentenceTransformer(model_name)
    _MODEL_CACHE[model_name] = model
    return model


def _provider_embedding(text: str, model_name: str) -> tuple[List[float] | None, dict[str, str]]:
    selection = model_router.get_provider_for_role("embedding")
    if not selection.provider_id:
        return None, {}
    response = provider_execute.execute_embedding_call(selection, text)
    embedding = response.get("embedding") if isinstance(response, dict) else None
    meta = {
        "provider_id": str(getattr(selection, "provider_id", "") or ""),
        "model": str(model_name or getattr(selection, "model", "") or ""),
    }
    if isinstance(embedding, list):
        return [float(x) for x in embedding], meta
    return None, meta


def generate_embedding(text: str) -> List[float] | None:
    emit_event(LOGFILE, "brain_embedding_start", status="ok")
    if not isinstance(text, str) or not text.strip():
        emit_event(LOGFILE, "brain_embedding_failed", status="error", reason="empty_text")
        return None
    local_model = getattr(config, "BRAIN_EMBED_MODEL_LOCAL", "simple")
    provider_model = getattr(config, "BRAIN_EMBED_MODEL_PROVIDER", "")
    if local_model:
        if local_model in {"simple", "hash"}:
            embedding = _simple_embedding(text)
            emit_event(LOGFILE, "brain_embedding_complete", status="ok", provider="local_simple")
            emit_event(LOGFILE, "brain_embedding_generated", status="ok", provider="local_simple", dimensions=len(embedding))
            return embedding
        model = _load_sentence_transformer(local_model)
        if model is not None:
            embedding = model.encode([text])[0]
            emit_event(LOGFILE, "brain_embedding_complete", status="ok", provider="local_model")
            vector = [float(x) for x in embedding]
            emit_event(LOGFILE, "brain_embedding_generated", status="ok", provider="local_model", dimensions=len(vector))
            return vector
    if provider_model:
        try:
            embedding, provider_meta = _provider_embedding(text, provider_model)
        except Exception:
            embedding, provider_meta = None, {}
        if embedding:
            emit_event(
                LOGFILE,
                "brain_embedding_complete",
                status="ok",
                provider="provider",
                provider_id=provider_meta.get("provider_id", ""),
                model=provider_meta.get("model", ""),
            )
            emit_event(
                LOGFILE,
                "brain_embedding_generated",
                status="ok",
                provider="provider",
                dimensions=len(embedding),
                provider_id=provider_meta.get("provider_id", ""),
                model=provider_meta.get("model", ""),
            )
            return embedding
    emit_event(LOGFILE, "brain_embedding_failed", status="error", reason="no_embedding")
    return None
