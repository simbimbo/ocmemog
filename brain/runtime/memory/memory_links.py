from __future__ import annotations

import sqlite3
from typing import Dict, List

from ocmemog.runtime import state_store
from ocmemog.runtime.instrumentation import emit_event
from ocmemog.runtime.memory import store

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def _dedupe_memory_links(conn) -> None:
    conn.execute(
        """
        DELETE FROM memory_links
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM memory_links
            GROUP BY source_reference, link_type, target_reference
        )
        """
    )


def _ensure_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_links (
            source_reference TEXT NOT NULL,
            link_type TEXT NOT NULL,
            target_reference TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source_reference, link_type, target_reference)
        )
        """
    )
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_links_unique ON memory_links(source_reference, link_type, target_reference)"
        )
    except sqlite3.IntegrityError:
        _dedupe_memory_links(conn)
        conn.execute("DROP INDEX IF EXISTS idx_memory_links_unique")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_links_unique ON memory_links(source_reference, link_type, target_reference)"
        )
        conn.commit()


def add_memory_link(source_reference: str, link_type: str, target_reference: str) -> None:
    conn = store.connect()
    _ensure_table(conn)
    conn.execute(
        "INSERT OR IGNORE INTO memory_links (source_reference, link_type, target_reference) VALUES (?, ?, ?)",
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


def get_memory_links_for_target(target_reference: str) -> List[Dict[str, str]]:
    conn = store.connect()
    _ensure_table(conn)
    rows = conn.execute(
        "SELECT source_reference, link_type, target_reference FROM memory_links WHERE target_reference=? ORDER BY created_at DESC",
        (target_reference,),
    ).fetchall()
    conn.close()
    return [
        {
            "source_reference": row[0],
            "link_type": row[1],
            "target_reference": row[2],
        }
        for row in rows
    ]


def get_memory_links_for_thread(thread_id: str) -> List[Dict[str, str]]:
    return get_memory_links_for_target(f"thread:{thread_id}")


def get_memory_links_for_session(session_id: str) -> List[Dict[str, str]]:
    return get_memory_links_for_target(f"session:{session_id}")


def get_memory_links_for_conversation(conversation_id: str) -> List[Dict[str, str]]:
    return get_memory_links_for_target(f"conversation:{conversation_id}")


def count_memory_links() -> int:
    conn = store.connect()
    _ensure_table(conn)
    row = conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()
    conn.close()
    return int(row[0]) if row else 0
