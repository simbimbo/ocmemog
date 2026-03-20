"""Native role registry surfaced as an engine-like runtime surface.

The upstream brAIn project exposes role-prioritized context behavior through a
`roles` module. This repo keeps a compatible shape in `ocmemog` so callers can
query role metadata without being coupled to legacy implementation details.
"""

from __future__ import annotations

from typing import Dict, List

# Core roles are intentionally conservative and deterministic so they can be used
# by fallback context builders and lightweight policy checks.
ROLE_REGISTRY: Dict[str, Dict[str, object]] = {
    "default": {
        "priority": 90,
        "description": "Balanced fallback role for generic memory lookup",
        "ordered_buckets": (
            "knowledge",
            "preferences",
            "identity",
            "reflections",
            "directives",
            "tasks",
            "runbooks",
            "lessons",
        ),
        "soft_window": 5,
    },
    "user": {
        "priority": 100,
        "description": "User-sourced prompts should prioritize reflective and identity buckets",
        "ordered_buckets": (
            "reflections",
            "identity",
            "preferences",
            "knowledge",
            "tasks",
            "directives",
            "runbooks",
            "lessons",
        ),
        "soft_window": 6,
    },
    "assistant": {
        "priority": 80,
        "description": "Assistant-sourced prompts should prioritize concise operational memory",
        "ordered_buckets": (
            "tasks",
            "directives",
            "knowledge",
            "runbooks",
            "lessons",
            "preferences",
            "reflections",
            "identity",
        ),
        "soft_window": 4,
    },
}

__all__ = ["ROLE_REGISTRY", "role_registry", "role_profile", "sorted_roles"]


def role_registry() -> Dict[str, Dict[str, object]]:
    """Return the current role registry as a copy for safe introspection."""

    return {
        key: {
            "role": key,
            **{
                "buckets": tuple(profile.get("ordered_buckets") or ()),
                "priority": int(profile.get("priority", 0)),
                "description": str(profile.get("description", "")),
                "soft_window": int(profile.get("soft_window", 0)),
            },
        }
        for key, profile in ROLE_REGISTRY.items()
    }


def role_profile(role: str) -> Dict[str, object] | None:
    """Return a normalized profile for a single role, if defined."""

    normalized = str(role or "").strip().lower() or "default"
    return role_registry().get(normalized)


def sorted_roles() -> List[str]:
    """Return role ids ordered by descending priority."""

    return sorted((role_registry().keys()), key=lambda role_id: role_registry()[role_id]["priority"], reverse=True)
