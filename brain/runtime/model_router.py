from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSelection:
    provider_id: str = ""
    model: str = ""


def get_provider_for_role(_role: str) -> ModelSelection:
    return ModelSelection()
