from __future__ import annotations

from brain.runtime import config


def get_model_for_role(role: str) -> str:
    if role == "memory":
        return config.OCMEMOG_MEMORY_MODEL
    if role == "embedding":
        return config.OCMEMOG_OPENAI_EMBED_MODEL
    return config.OCMEMOG_MEMORY_MODEL
