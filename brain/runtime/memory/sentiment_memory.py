from __future__ import annotations

import sqlite3
import time
from typing import Dict, List

from brain.runtime import state_store
from brain.runtime.instrumentation import emit_event
from brain.runtime.memory import person_memory

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"

SENTIMENTS = {"positive", "neutral", "negative", "frustrated", "urgent", "excited"}


def classify_sentiment(text: str) -> str:
    lowered = (text or "").lower()
    if "urgent" in lowered:
        return "urgent"
    if "frustrated" in lowered or "angry" in lowered:
        return "frustrated"
    if "excited" in lowered or "great" in lowered:
        return "excited"
    if "bad" in lowered:
        return "negative"
    if "good" in lowered:
        return "positive"
    return "neutral"


def _connect() -> sqlite3.Connection:
    path = state_store.data_dir() / "sentiment_memory.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sentiment_memory (
            person_id TEXT,
            sentiment TEXT,
            timestamp TEXT
        )
        """
    )
    return conn


def update_person_sentiment_baseline(person_id: str, sentiment: str) -> None:
    sentiment = sentiment if sentiment in SENTIMENTS else "neutral"
    conn = _connect()
    conn.execute(
        "INSERT INTO sentiment_memory (person_id, sentiment, timestamp) VALUES (?, ?, ?)",
        (person_id, sentiment, time.strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()
    emit_event(LOGFILE, "brain_person_sentiment_updated", status="ok", person_id=person_id)


def list_sentiment(person_id: str, limit: int = 10) -> List[Dict[str, str]]:
    conn = _connect()
    rows = conn.execute(
        "SELECT sentiment, timestamp FROM sentiment_memory WHERE person_id=? ORDER BY timestamp DESC LIMIT ?",
        (person_id, limit),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
