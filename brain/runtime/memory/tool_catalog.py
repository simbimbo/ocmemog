from __future__ import annotations

import json
from typing import Dict, Any

from brain.runtime.instrumentation import emit_event
from brain.runtime import state_store
from brain.runtime.memory import store


def _ensure_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_catalog (
            tool_id TEXT PRIMARY KEY,
            description TEXT,
            permission_class TEXT,
            capability_tags TEXT,
            first_seen TEXT DEFAULT (datetime('now')),
            last_used TEXT
        )
        """
    )


def record_tool_metadata(metadata: Dict[str, Any]) -> None:
    conn = store.connect()
    _ensure_table(conn)
    tool_id = metadata.get("tool_id")
    description = metadata.get("description", "")
    permission_class = metadata.get("permission_class", "restricted")
    capability_tags = json.dumps(metadata.get("capability_tags", []) or [])
    conn.execute(
        """
        INSERT INTO tool_catalog (tool_id, description, permission_class, capability_tags)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(tool_id) DO UPDATE SET
            description=excluded.description,
            permission_class=excluded.permission_class,
            capability_tags=excluded.capability_tags
        """,
        (tool_id, description, permission_class, capability_tags),
    )
    conn.commit()
    conn.close()
    emit_event(
        state_store.reports_dir() / "brain_memory.log.jsonl",
        "brain_memory_tool_catalog_update",
        status="ok",
        tool_id=tool_id,
    )


def record_tool_usage(tool_id: str) -> None:
    conn = store.connect()
    _ensure_table(conn)
    conn.execute(
        "UPDATE tool_catalog SET last_used=datetime('now') WHERE tool_id=?",
        (tool_id,),
    )
    conn.commit()
    conn.close()
    emit_event(
        state_store.reports_dir() / "brain_memory.log.jsonl",
        "brain_memory_tool_catalog_update",
        status="ok",
        tool_id=tool_id,
    )
