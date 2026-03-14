from __future__ import annotations

import sqlite3
from typing import Dict, List

from brain.runtime import state_store
from brain.runtime.instrumentation import emit_event

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def _connect() -> sqlite3.Connection:
    path = state_store.data_dir() / "memory_graph.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_graph (
            source_reference TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            target_reference TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source_reference, edge_type, target_reference)
        )
        """
    )
    return conn


def add_memory_edge(source_reference: str, edge_type: str, target_reference: str) -> None:
    conn = _connect()
    conn.execute(
        "INSERT OR IGNORE INTO memory_graph (source_reference, edge_type, target_reference) VALUES (?, ?, ?)",
        (source_reference, edge_type, target_reference),
    )
    conn.commit()
    conn.close()
    emit_event(LOGFILE, "brain_memory_graph_edge_created", status="ok", edge_type=edge_type)


def get_neighbors(source_reference: str) -> List[Dict[str, str]]:
    conn = _connect()
    rows = conn.execute(
        "SELECT edge_type, target_reference FROM memory_graph WHERE source_reference=?",
        (source_reference,),
    ).fetchall()
    conn.close()
    return [{"edge_type": row[0], "target_reference": row[1]} for row in rows]


def get_cluster(source_reference: str, limit: int = 5) -> List[str]:
    neighbors = get_neighbors(source_reference)
    return [item["target_reference"] for item in neighbors[:limit]]
