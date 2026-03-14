from __future__ import annotations

from dataclasses import dataclass

from brain.runtime import config


@dataclass(frozen=True)
class ModelSelection:
    provider_id: str = ""
    model: str = ""


def get_provider_for_role(role: str) -> ModelSelection:
    if role != "embedding":
        return ModelSelection()
    provider = (config.BRAIN_EMBED_MODEL_PROVIDER or "").strip().lower()
    if provider in {"openai", "openai_compatible", "openai-compatible"}:
        return ModelSelection(provider_id="openai", model=config.OCMEMOG_OPENAI_EMBED_MODEL)
    return ModelSelection()
