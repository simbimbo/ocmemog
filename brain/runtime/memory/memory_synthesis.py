from __future__ import annotations

from typing import Dict, List

from brain.runtime import state_store
from brain.runtime.instrumentation import emit_event
from brain.runtime.memory import reinforcement

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


SYNTHESIS_TYPES = [
    "theme_summary",
    "user_preference",
    "candidate_procedure",
    "recurring_pattern",
    "contradiction_candidate",
]


def synthesize_memory_patterns(limit: int = 5) -> List[Dict[str, str]]:
    emit_event(LOGFILE, "brain_memory_synthesis_start", status="ok")
    stats = reinforcement.list_recent_experiences(limit=limit)
    results: List[Dict[str, str]] = []
    for key, count in stats.items():
        results.append(
            {
                "type": "recurring_pattern",
                "summary": f"{key} occurred {count} times",
            }
        )
    emit_event(LOGFILE, "brain_memory_synthesis_complete", status="ok", count=len(results))
    return results[:limit]
