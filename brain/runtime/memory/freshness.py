from __future__ import annotations

import time
from typing import Any, Dict, List

from brain.runtime import state_store
from brain.runtime.instrumentation import emit_event
from brain.runtime.memory import store

DEFAULT_STALE_DAYS = 30
DEFAULT_CONFIDENCE_THRESHOLD = 0.6
DEFAULT_LIMIT = 25


def scan_freshness(
    stale_days: int = DEFAULT_STALE_DAYS,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    emit_event(
        state_store.reports_dir() / "brain_memory.log.jsonl",
        "brain_memory_freshness_scan_start",
        status="ok",
        stale_days=stale_days,
        confidence_threshold=confidence_threshold,
    )
    conn = store.connect()
    stale_rows = conn.execute(
        """
        SELECT 'knowledge' AS memory_type, id, timestamp, confidence, content
        FROM knowledge
        WHERE timestamp <= datetime('now', ?)
        ORDER BY timestamp ASC
        LIMIT ?
        """,
        (f"-{max(1, stale_days)} days", limit),
    ).fetchall()
    low_conf_rows = conn.execute(
        """
        SELECT 'knowledge' AS memory_type, id, timestamp, confidence, content
        FROM knowledge
        WHERE confidence < ?
        ORDER BY confidence ASC, timestamp ASC
        LIMIT ?
        """,
        (confidence_threshold, limit),
    ).fetchall()
    conn.close()
    advisories: List[Dict[str, Any]] = []
    refresh_candidates: List[Dict[str, Any]] = []
    now_ts = time.time()
    for category, rows in (("stale", stale_rows), ("low_confidence", low_conf_rows)):
        for row in rows:
            age_seconds = 0.0
            if row["timestamp"]:
                try:
                    age_seconds = max(0.0, now_ts - time.mktime(time.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")))
                except Exception:
                    age_seconds = 0.0
            confidence = float(row["confidence"] or 0.0)
            freshness_score = max(0.0, 1.0 - min(age_seconds / (stale_days * 86400), 1.0)) * (0.5 + confidence / 2.0)
            refresh_recommended = freshness_score < 0.4 or category == "stale"
            entry = {
                "category": category,
                "memory_type": row["memory_type"],
                "memory_id": row["id"],
                "timestamp": row["timestamp"],
                "confidence": confidence,
                "summary": str(row["content"])[:120],
                "freshness_score": round(freshness_score, 3),
                "refresh_recommended": refresh_recommended,
            }
            advisories.append(entry)
            refresh_candidates.append(entry)
    emit_event(
        state_store.reports_dir() / "brain_memory.log.jsonl",
        "brain_memory_freshness_scan_complete",
        status="ok",
        advisory_count=len(advisories),
        refresh_candidates=len(refresh_candidates),
    )
    return {
        "ok": True,
        "advisory_only": True,
        "advisories": advisories,
        "refresh_candidates": refresh_candidates,
    }


def freshness_weight(score: float) -> float:
    return max(0.0, min(1.0, float(score)))
