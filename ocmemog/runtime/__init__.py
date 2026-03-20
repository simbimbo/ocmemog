"""ocmemog-native runtime namespace backed by the legacy brain runtime."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType

from . import identity as identity


def _alias_module(alias: str, target: str) -> ModuleType:
    module = importlib.import_module(target)
    sys.modules.setdefault(alias, module)
    return module


_runtime = importlib.import_module("brain.runtime")

config = _alias_module(__name__ + ".config", "brain.runtime.config")
inference = _alias_module(__name__ + ".inference", "brain.runtime.inference")
instrumentation = _alias_module(__name__ + ".instrumentation", "brain.runtime.instrumentation")
model_roles = _alias_module(__name__ + ".model_roles", "brain.runtime.model_roles")
model_router = _alias_module(__name__ + ".model_router", "brain.runtime.model_router")
providers = _alias_module(__name__ + ".providers", "brain.runtime.providers")
state_store = _alias_module(__name__ + ".state_store", "brain.runtime.state_store")
storage_paths = _alias_module(__name__ + ".storage_paths", "brain.runtime.storage_paths")
roles = importlib.import_module("ocmemog.runtime.roles")

__all__ = [
    "config",
    "inference",
    "instrumentation",
    "model_roles",
    "model_router",
    "providers",
    "identity",
    "roles",
    "state_store",
    "storage_paths",
]


def __getattr__(name: str):
    return getattr(_runtime, name)
