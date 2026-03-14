from __future__ import annotations

import os
from typing import Dict

from brain.runtime import state_store
from brain.runtime.instrumentation import emit_event

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"

DIRECT_THRESHOLD = float(os.environ.get("BRAIN_MEMORY_GATE_DIRECT", 1.5))
ASSIST_THRESHOLD = float(os.environ.get("BRAIN_MEMORY_GATE_ASSIST", 0.8))


def decide_gate(result: Dict[str, float]) -> Dict[str, float | str]:
    similarity = float(result.get("similarity", result.get("score", 0.0)))
    reinforcement = float(result.get("reinforcement_weight", 0.0))
    freshness = float(result.get("freshness", 0.0))
    promotion = float(result.get("promotion_confidence", 0.0))
    score = similarity + reinforcement + freshness + promotion
    if score >= DIRECT_THRESHOLD:
        decision = "memory_direct"
    elif score >= ASSIST_THRESHOLD:
        decision = "memory_assisted"
    else:
        decision = "model_escalation"
    payload = {
        "decision": decision,
        "score": round(score, 3),
        "similarity": similarity,
        "reinforcement_weight": reinforcement,
        "freshness": freshness,
        "promotion_confidence": promotion,
        "salience_score": float(result.get("salience_score", 0.0)),
    }
    emit_event(LOGFILE, "brain_memory_gate_decision", status="ok", decision=decision, score=round(score, 3))
    emit_event(LOGFILE, "brain_memory_gate_score", status="ok", score=round(score, 3))
    return payload
