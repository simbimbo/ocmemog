"""Runtime state persistence surface owned by ocmemog."""

from __future__ import annotations

from pathlib import Path

from . import storage_paths

__wrapped_from__ = "brain.runtime.state_store"


def root_dir() -> Path:
    return storage_paths.root_dir()


def data_dir() -> Path:
    return storage_paths.data_dir()


def memory_dir() -> Path:
    return storage_paths.memory_dir()


def reports_dir() -> Path:
    return storage_paths.reports_dir()


def memory_db_path() -> Path:
    return storage_paths.memory_db_path()
