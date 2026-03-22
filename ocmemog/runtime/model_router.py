"""Model-provider routing helpers owned by ocmemog."""

from __future__ import annotations

from dataclasses import dataclass

from . import config



@dataclass(frozen=True)
class ModelSelection:
    provider_id: str = ""
    model: str = ""


def get_provider_for_role(role: str) -> ModelSelection:
    if role != "embedding":
        return ModelSelection()
    provider = (config.OCMEMOG_EMBED_PROVIDER or config.BRAIN_EMBED_MODEL_PROVIDER or "").strip().lower()
    if provider in {"openai", "openai_compatible", "openai-compatible"}:
        return ModelSelection(provider_id="openai", model=config.OCMEMOG_OPENAI_EMBED_MODEL)
    if provider in {"local-openai", "local_openai", "llamacpp", "llama.cpp"}:
        return ModelSelection(provider_id="local-openai", model=config.OCMEMOG_LOCAL_EMBED_MODEL)
    if provider in {"ollama", "local-ollama"}:
        return ModelSelection(provider_id="ollama", model=config.OCMEMOG_OLLAMA_EMBED_MODEL)
    return ModelSelection()

