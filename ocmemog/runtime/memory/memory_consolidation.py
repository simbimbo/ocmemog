from __future__ import annotations

from typing import Dict, List, Tuple

from ocmemog.runtime import state_store
from ocmemog.runtime.instrumentation import emit_event
from ocmemog.runtime.memory import memory_taxonomy

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def _cluster_key(record: Dict[str, object]) -> Tuple[str, str]:
    mem_type = memory_taxonomy.classify_memory_type(record)
    content = str(record.get("content") or "")
    anchor = content[:48].lower()
    return mem_type, anchor


def consolidate_memories(records: List[Dict[str, object]], max_clusters: int = 5) -> Dict[str, object]:
    emit_event(LOGFILE, "brain_memory_consolidation_start", status="ok")
    clusters: Dict[Tuple[str, str], List[Dict[str, object]]] = {}
    for record in records:
        content = str(record.get("content") or "").strip()
        if not content:
            continue
        key = _cluster_key(record)
        clusters.setdefault(key, []).append(record)
        if len(clusters) >= max_clusters:
            break
    consolidated: List[Dict[str, object]] = []
    reinforcement_updates: List[Dict[str, object]] = []
    for key, items in clusters.items():
        mem_type, anchor = key
        summary = f"{mem_type} cluster: {anchor}"
        references = [str(item.get("reference") or "") for item in items if str(item.get("reference") or "")]
        consolidated.append(
            {
                "memory_type": mem_type,
                "summary": summary,
                "count": len(items),
                "references": references,
                "candidate_kinds": sorted({str(item.get("candidate_kind") or "memory") for item in items}),
            }
        )
        reinforcement_updates.append(
            {
                "memory_type": mem_type,
                "weight": min(1.0, len(items) / 5.0),
                "references": references,
            }
        )
        emit_event(
            LOGFILE,
            "brain_memory_consolidation_cluster",
            status="ok",
            memory_type=mem_type,
            count=len(items),
        )
    emit_event(LOGFILE, "brain_memory_consolidation_complete", status="ok", cluster_count=len(consolidated))
    return {"consolidated": consolidated, "reinforcement": reinforcement_updates}
