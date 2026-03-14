from __future__ import annotations

from typing import Dict, List, Tuple

from brain.runtime import state_store
from brain.runtime.instrumentation import emit_event
from brain.runtime.memory import memory_taxonomy

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def _cluster_key(record: Dict[str, object]) -> Tuple[str, str]:
    mem_type = memory_taxonomy.classify_memory_type(record)
    content = str(record.get("content") or "")
    anchor = content[:32].lower()
    return mem_type, anchor


def consolidate_memories(records: List[Dict[str, object]], max_clusters: int = 5) -> Dict[str, object]:
    emit_event(LOGFILE, "brain_memory_consolidation_start", status="ok")
    clusters: Dict[Tuple[str, str], List[Dict[str, object]]] = {}
    for record in records:
        key = _cluster_key(record)
        clusters.setdefault(key, []).append(record)
        if len(clusters) >= max_clusters:
            break
    consolidated: List[Dict[str, object]] = []
    reinforcement_updates: List[Dict[str, object]] = []
    for key, items in clusters.items():
        mem_type, anchor = key
        summary = f"{mem_type} cluster: {anchor}"
        consolidated.append({"memory_type": mem_type, "summary": summary, "count": len(items)})
        reinforcement_updates.append({"memory_type": mem_type, "weight": min(1.0, len(items) / 5.0)})
        emit_event(LOGFILE, "brain_memory_consolidation_cluster", status="ok", memory_type=mem_type, count=len(items))
    emit_event(LOGFILE, "brain_memory_consolidation_complete", status="ok", cluster_count=len(consolidated))
    return {"consolidated": consolidated, "reinforcement": reinforcement_updates}
