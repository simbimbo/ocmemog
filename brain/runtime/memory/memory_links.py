from __future__ import annotations

from typing import Dict, List

from brain.runtime import state_store
from brain.runtime.instrumentation import emit_event
from brain.runtime.memory import store

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def _ensure_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_links (
            source_reference TEXT NOT NULL,
            link_type TEXT NOT NULL,
            target_reference TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def add_memory_link(source_reference: str, link_type: str, target_reference: str) -> None:
    conn = store.connect()
    _ensure_table(conn)
    conn.execute(
        "INSERT INTO memory_links (source_reference, link_type, target_reference) VALUES (?, ?, ?)",
        (source_reference, link_type, target_reference),
    )
    conn.commit()
    conn.close()
    emit_event(LOGFILE, "brain_memory_link_created", status="ok", link_type=link_type)


def get_memory_links(source_reference: str) -> List[Dict[str, str]]:
    conn = store.connect()
    _ensure_table(conn)
    rows = conn.execute(
        "SELECT link_type, target_reference FROM memory_links WHERE source_reference=?",
        (source_reference,),
    ).fetchall()
    conn.close()
    return [{"link_type": row[0], "target_reference": row[1]} for row in rows]


def count_memory_links() -> int:
    conn = store.connect()
    _ensure_table(conn)
    row = conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()
    conn.close()
    return int(row[0]) if row else 0
