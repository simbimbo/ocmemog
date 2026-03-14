from __future__ import annotations

import os
from pathlib import Path


def root_dir() -> Path:
    configured = os.environ.get("OCMEMOG_STATE_DIR") or os.environ.get("BRAIN_STATE_DIR")
    base = Path(configured).expanduser() if configured else Path.home() / ".ocmemog"
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
    override = os.environ.get("OCMEMOG_DB_PATH")
    if override:
        path = Path(override).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    return memory_dir() / "brain_memory.sqlite3"
