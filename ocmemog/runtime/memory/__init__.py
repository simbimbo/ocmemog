"""ocmemog-native memory namespace with native surfaces."""

from __future__ import annotations

import importlib

# Native-first memory surfaces (core path has no package-level wrapper bootstrapping).
from . import (
    api,
    candidate,
    memory_consolidation,
    distill,
    embedding_engine,
    health,
    integrity,
    memory_taxonomy,
    memory_links,
    provenance,
    promote,
    retrieval,
    store,
    unresolved_state,
    vector_index,
)

_NATIVE_MEMORY_SURFACES = (
    "api",
    "candidate",
    "distill",
    "embedding_engine",
    "health",
    "integrity",
    "memory_consolidation",
    "memory_taxonomy",
    "memory_links",
    "provenance",
    "promote",
    "retrieval",
    "store",
    "unresolved_state",
    "vector_index",
)

_MEMORY_SURFACES = (
    "conversation_state",
    "memory_synthesis",
    "pondering_engine",
    "reinforcement",
    "semantic_search",
    "memory_salience",
    "freshness",
)


def __getattr__(name: str):
    if name in _NATIVE_MEMORY_SURFACES:
        return globals()[name]
    if name in _MEMORY_SURFACES:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(name)


__all__ = [
    "api",
    "candidate",
    "conversation_state",
    "distill",
    "embedding_engine",
    "health",
    "integrity",
    "memory_links",
    "memory_consolidation",
    "memory_taxonomy",
    "memory_synthesis",
    "promote",
    "provenance",
    "pondering_engine",
    "freshness",
    "memory_salience",
    "reinforcement",
    "retrieval",
    "semantic_search",
    "store",
    "unresolved_state",
    "vector_index",
]

# No legacy-shimmed memory surfaces remain in this namespace.
__legacy_memory_surfaces__ = ()
