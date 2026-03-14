from __future__ import annotations

from typing import Dict, List

from brain.runtime import state_store
from brain.runtime.instrumentation import emit_event
from brain.runtime.memory import unresolved_state, memory_consolidation, memory_links

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def run_ponder_cycle(max_items: int = 5) -> Dict[str, object]:
    emit_event(LOGFILE, "brain_ponder_cycle_start", status="ok")
    unresolved = unresolved_state.list_unresolved_state(limit=max_items)
    consolidation = memory_consolidation.consolidate_memories([], max_clusters=max_items)
    insights: List[Dict[str, object]] = []
    for item in unresolved[:max_items]:
        insights.append({"type": "unresolved", "summary": item.get("summary", "")})
        emit_event(LOGFILE, "brain_ponder_insight_generated", status="ok")
    links: List[Dict[str, object]] = []
    for cluster in consolidation.get("consolidated", []):
        links.append({"type": "cluster", "summary": cluster.get("summary")})
        memory_links.add_memory_link("ponder", "conceptual", str(cluster.get("summary")))
    emit_event(LOGFILE, "brain_ponder_cycle_complete", status="ok")
    return {
        "unresolved": unresolved,
        "insights": insights,
        "links": links,
    }
