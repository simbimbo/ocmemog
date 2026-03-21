"""Model-role mapping helpers owned by ocmemog."""

from __future__ import annotations

from . import config

__wrapped_from__ = "brain.runtime.model_roles"


def get_model_for_role(role: str) -> str:
    if role == "memory":
        return config.OCMEMOG_MEMORY_MODEL
    if role == "embedding":
        return config.OCMEMOG_OPENAI_EMBED_MODEL
    return config.OCMEMOG_MEMORY_MODEL

