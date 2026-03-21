"""Internal helpers for explicitly wrapping legacy `brain.runtime` modules."""

from __future__ import annotations

import importlib
import sys
import types
from types import ModuleType


def wrap_legacy_module(alias: str, legacy_name: str) -> ModuleType:
    """Return a module object exposing `legacy_name` under `alias`.

    This keeps import semantics stable (`import ocmemog.runtime.xxx`) while preserving
    a native module identity for ocmemog surfaces.
    """

    legacy = importlib.import_module(legacy_name)
    module = types.ModuleType(alias)
    module.__dict__.update(legacy.__dict__)
    module.__dict__["__name__"] = alias
    module.__dict__["__package__"] = alias.rpartition(".")[0]
    module.__dict__["__wrapped_from__"] = legacy_name
    module.__dict__["__wrapped_module__"] = legacy
    module.__dict__["__wrapped_by__"] = "ocmemog-runtime-bridge"
    sys.modules[alias] = module
    return module

