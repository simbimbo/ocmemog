from __future__ import annotations

from typing import Dict, List

from brain.runtime import state_store
from brain.runtime.instrumentation import emit_event

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"

MEMORY_TYPES = [
    "episodic",
    "semantic",
    "procedural",
    "relationship",
    "working",
]


def list_memory_types() -> List[str]:
    return list(MEMORY_TYPES)


def classify_memory_type(record: Dict[str, object]) -> str:
    content = str(record.get("content") or "")
    memory_type = "semantic"
    if "how to" in content.lower() or "step" in content.lower():
        memory_type = "procedural"
    elif "met" in content.lower() or "relationship" in content.lower():
        memory_type = "relationship"
    elif record.get("memory_type") in MEMORY_TYPES:
        memory_type = str(record.get("memory_type"))
    elif record.get("source") == "working":
        memory_type = "working"
    emit_event(LOGFILE, "brain_memory_taxonomy_assigned", status="ok", memory_type=memory_type)
    return memory_type
