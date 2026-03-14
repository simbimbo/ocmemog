from __future__ import annotations

from typing import Dict, List

from brain.runtime import state_store
from brain.runtime.instrumentation import emit_event
from brain.runtime.memory import freshness

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def score_salience(record: Dict[str, float]) -> Dict[str, float | bool]:
    importance = float(record.get("importance", 0.2))
    novelty = float(record.get("novelty", 0.1))
    uncertainty = float(record.get("uncertainty", 0.1))
    risk = float(record.get("risk", 0.0))
    goal_alignment = float(record.get("goal_alignment", 0.1))
    reinforcement = float(record.get("reinforcement", 0.0))
    user_interest = float(record.get("user_interest", 0.0))
    recency = float(record.get("freshness", 0.0))
    signal_priority = float(record.get("signal_priority", 0.0))
    salience_score = max(
        0.0,
        min(3.0, importance + novelty + uncertainty + risk + goal_alignment + reinforcement + user_interest + recency + signal_priority),
    )
    activation_strength = min(1.0, salience_score / 3.0)
    attention_trigger = salience_score >= 1.5
    emit_event(LOGFILE, "brain_memory_salience_scored", status="ok", score=salience_score)
    emit_event(LOGFILE, "brain_memory_salience_updated", status="ok", score=salience_score)
    return {
        "salience_score": round(salience_score, 3),
        "activation_strength": round(activation_strength, 3),
        "attention_trigger": attention_trigger,
    }


def scan_salient_memories(limit: int = 5) -> List[Dict[str, float | bool]]:
    advisories = freshness.scan_freshness(limit=limit).get("advisories", [])
    results = []
    for item in advisories:
        score = score_salience({"freshness": float(item.get("freshness_score", 0.0))})
        if score.get("attention_trigger"):
            results.append(score)
    return results[:limit]
