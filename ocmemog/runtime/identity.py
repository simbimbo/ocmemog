"""Runtime identity and capability ownership metadata for ocmemog runtime surfaces."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, List

from importlib import import_module


ENGINE_ID = "ocmemog-native"
SURFACE_ENGINE_OWNER = "ocmemog-native"
SURFACE_COMPAT_OWNER = "brain-runtime-shim"
SURFACE_MISSING = "missing"

# Ordered list keeps output stable for deterministic status payloads.
_RUNTIME_SURFACES = (
    "ocmemog.runtime.roles",
    "ocmemog.runtime.identity",
    "ocmemog.runtime.config",
    "ocmemog.runtime.inference",
    "ocmemog.runtime.model_router",
    "ocmemog.runtime.model_roles",
    "ocmemog.runtime.providers",
    "ocmemog.runtime.state_store",
    "ocmemog.runtime.storage_paths",
)

_SURFACE_DETAILS = {
    "ocmemog.runtime.roles": "Native role registry for context/priority metadata",
    "ocmemog.runtime.identity": "Identity and capability ownership manifest",
    "ocmemog.runtime.config": "Configuration surface for memory/model providers",
    "ocmemog.runtime.inference": "Inference orchestration helper",
    "ocmemog.runtime.model_router": "Model routing helper",
    "ocmemog.runtime.model_roles": "Model-role mapping helper",
    "ocmemog.runtime.providers": "Provider execution surface for embeddings",
    "ocmemog.runtime.state_store": "Runtime state persistence surface",
    "ocmemog.runtime.storage_paths": "Storage path helpers",
}


def _surface_owner(module_name: str) -> tuple[str, str, str]:
    """Return (owner, provider_module, status) for an import target."""

    try:
        module = import_module(module_name)
    except Exception as exc:  # pragma: no cover - defensive surface.
        return (SURFACE_MISSING, "<import-failed>", f"{type(exc).__name__}: {exc}")

    wrapped_from = getattr(module, "__wrapped_from__", None)
    wrapped_by = getattr(module, "__wrapped_by__", None)

    provider_module = module.__name__
    owner = SURFACE_ENGINE_OWNER

    if isinstance(wrapped_from, str) and wrapped_from.strip():
        owner = SURFACE_COMPAT_OWNER
        provider_module = wrapped_from.strip()
    elif isinstance(wrapped_by, str) and wrapped_by.strip():
        owner = SURFACE_COMPAT_OWNER
    elif not provider_module.startswith("ocmemog."):
        owner = SURFACE_COMPAT_OWNER

    return (owner, provider_module, "ok")


def get_capability_ownership() -> List[Dict[str, Any]]:
    """Return a stable capability ownership matrix for runtime surfaces."""

    owned: List[Dict[str, Any]] = []
    for surface in _RUNTIME_SURFACES:
        owner, provider_module, status = _surface_owner(surface)
        owned.append(
            {
                "surface": surface,
                "provider_module": provider_module,
                "owner": owner,
                "status": "ready" if owner != SURFACE_MISSING else "missing",
                "status_detail": status,
                "description": _SURFACE_DETAILS.get(surface, "runtime surface"),
            }
        )
    return owned


def get_runtime_identity() -> Dict[str, Any]:
    """Return a compact runtime-identity payload."""

    capabilities = get_capability_ownership()
    grouped = OrderedDict([
        (SURFACE_ENGINE_OWNER, []),
        (SURFACE_COMPAT_OWNER, []),
        (SURFACE_MISSING, []),
    ])
    for item in capabilities:
        grouped.setdefault(item["owner"], []).append(item["surface"])

    return {
        "engine": ENGINE_ID,
        "schema": "ocmemog-runtime-identity-v1",
        "capabilities": capabilities,
        "capability_counts": {
            SURFACE_ENGINE_OWNER: len(grouped[SURFACE_ENGINE_OWNER]),
            SURFACE_COMPAT_OWNER: len(grouped[SURFACE_COMPAT_OWNER]),
            SURFACE_MISSING: len(grouped[SURFACE_MISSING]),
        },
        "surface_names": list(_RUNTIME_SURFACES),
    }


__all__ = [
    "ENGINE_ID",
    "get_capability_ownership",
    "get_runtime_identity",
]
