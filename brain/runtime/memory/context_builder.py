from __future__ import annotations

from typing import Dict, Iterable, List

from brain.runtime.instrumentation import emit_event
from brain.runtime import state_store
from brain.runtime.memory import retrieval


def build_context(
    prompt: str,
    max_context_blocks: int = 5,
    *,
    memory_queries: Iterable[str] | None = None,
    memory_priorities: Iterable[str] | None = None,
    role_id: str | None = None,
) -> Dict[str, List[str]]:
    emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_context_build_start", status="ok")
    queries = [query for query in (memory_queries or ()) if isinstance(query, str) and query.strip()]
    categories = [category for category in (memory_priorities or ()) if isinstance(category, str) and category.strip()]
    role_priorities: List[str] = []
    if role_id:
        try:
            from brain.runtime.roles import role_registry
            role = role_registry.get_role(role_id)
            role_priorities = list(role.memory_priority) if role else []
        except Exception:
            role_priorities = []
    combined_priorities = [*categories, *role_priorities]
    if queries:
        mem = retrieval.retrieve_for_queries(queries, categories=combined_priorities or None)
    else:
        mem = retrieval.retrieve(prompt, categories=combined_priorities or None)

    ranked_blocks: List[Dict[str, str | float]] = []
    for item in mem.get("knowledge", []):
        ranked_blocks.append(
            {
                "content": item.get("content"),
                "source": "knowledge",
                "score": float(item.get("score") or item.get("confidence") or 0.0),
            }
        )
    for item in mem.get("tasks", []):
        ranked_blocks.append(
            {
                "content": item.get("content"),
                "source": "tasks",
                "score": float(item.get("score") or item.get("confidence") or 0.0),
            }
        )
    if role_priorities:
        for item in ranked_blocks:
            if item.get("source") in role_priorities:
                item["score"] = float(item.get("score", 0.0)) + 0.2
        emit_event(
            state_store.reports_dir() / "brain_memory.log.jsonl",
            "brain_role_context_weighted",
            status="ok",
            role_id=role_id,
            priorities=len(role_priorities),
        )
    ranked_blocks.sort(key=lambda item: item.get("score", 0.0), reverse=True)
    if len(ranked_blocks) > max_context_blocks:
        ranked_blocks = ranked_blocks[:max_context_blocks]
        emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_context_trim", status="ok")

    context_blocks = [item["content"] for item in ranked_blocks if item.get("content")]
    context_scores = [item.get("score", 0.0) for item in ranked_blocks]
    synthesis = mem.get("synthesis", []) if isinstance(mem, dict) else []
    for item in synthesis[:2]:
        summary = item.get("summary") if isinstance(item, dict) else None
        if summary:
            context_blocks.append(str(summary))

    context = {
        "context_blocks": context_blocks,
        "context_scores": context_scores,
        "ranked_blocks": ranked_blocks,
        "knowledge": mem.get("knowledge", []),
        "tasks": mem.get("tasks", []),
        "directives": [item["content"] if isinstance(item, dict) else item for item in mem.get("directives", [])],
        "reflections": [item["content"] if isinstance(item, dict) else item for item in mem.get("reflections", [])],
        "used_queries": queries,
    }
    emit_event(state_store.reports_dir() / "brain_memory.log.jsonl", "brain_memory_context_build_complete", status="ok")
    return context
