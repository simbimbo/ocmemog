from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any

from brain.runtime import state_store
from brain.runtime.memory import store


def _artifact_dir() -> Path:
    path = state_store.memory_dir() / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def store_artifact(artifact_id: str, content: bytes, metadata: Dict[str, Any]) -> Path:
    path = _artifact_dir() / f"{artifact_id}.bin"
    path.write_bytes(content)
    content_hash = str(hash(content))
    conn = store.connect()
    conn.execute(
        "INSERT INTO artifacts (artifact_id, artifact_type, source_path, content_hash, metadata) VALUES (?, ?, ?, ?, ?)",
        (artifact_id, metadata.get("artifact_type", "unknown"), metadata.get("source_path", ""), content_hash, json.dumps(metadata)),
    )
    conn.commit()
    conn.close()
    return path


def load_artifact(artifact_id: str) -> bytes:
    path = _artifact_dir() / f"{artifact_id}.bin"
    return path.read_bytes()
