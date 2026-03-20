"""ocmemog-native memory namespace backed by the legacy brain runtime."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType


def _alias_module(alias: str, target: str) -> ModuleType:
    module = importlib.import_module(target)
    sys.modules.setdefault(alias, module)
    return module


_memory = importlib.import_module("brain.runtime.memory")

api = _alias_module(__name__ + ".api", "brain.runtime.memory.api")
conversation_state = _alias_module(__name__ + ".conversation_state", "brain.runtime.memory.conversation_state")
distill = _alias_module(__name__ + ".distill", "brain.runtime.memory.distill")
health = _alias_module(__name__ + ".health", "brain.runtime.memory.health")
memory_links = _alias_module(__name__ + ".memory_links", "brain.runtime.memory.memory_links")
pondering_engine = _alias_module(__name__ + ".pondering_engine", "brain.runtime.memory.pondering_engine")
provenance = _alias_module(__name__ + ".provenance", "brain.runtime.memory.provenance")
reinforcement = _alias_module(__name__ + ".reinforcement", "brain.runtime.memory.reinforcement")
retrieval = _alias_module(__name__ + ".retrieval", "brain.runtime.memory.retrieval")
store = _alias_module(__name__ + ".store", "brain.runtime.memory.store")
vector_index = _alias_module(__name__ + ".vector_index", "brain.runtime.memory.vector_index")

__all__ = [
    "api",
    "conversation_state",
    "distill",
    "health",
    "memory_links",
    "pondering_engine",
    "provenance",
    "reinforcement",
    "retrieval",
    "store",
    "vector_index",
]


def __getattr__(name: str):
    return getattr(_memory, name)
