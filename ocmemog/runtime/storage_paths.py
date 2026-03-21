"""Runtime storage path helpers owned by ocmemog."""

from __future__ import annotations

import os
from pathlib import Path


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    trimmed = raw.strip()
    if not trimmed:
        return None
    return Path(trimmed).expanduser().resolve()


def root_dir() -> Path:
    configured = _env_path("OCMEMOG_STATE_DIR") or _env_path("BRAIN_STATE_DIR")
    if configured:
        base = configured
    else:
        base = Path(__file__).resolve().parents[2] / ".ocmemog-state"
    base.mkdir(parents=True, exist_ok=True)
    return base


def data_dir() -> Path:
    path = root_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def memory_dir() -> Path:
    path = root_dir() / "memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def reports_dir() -> Path:
    path = root_dir() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def memory_db_path() -> Path:
    override = _env_path("OCMEMOG_DB_PATH")
    if override:
        override.parent.mkdir(parents=True, exist_ok=True)
        return override
    return memory_dir() / "brain_memory.sqlite3"
