from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

from brain.runtime.instrumentation import emit_event
from brain.runtime import state_store, inference
from brain.runtime.memory import retrieval

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def _heuristic_queries(prompt: str, limit: int = 3) -> List[str]:
    cleaned = re.sub(r"\s+", " ", prompt or "").strip()
    parts = re.split(r",| and | then | also ", cleaned)
    queries = []
    for part in parts:
        q = part.strip(" .")
        if len(q) >= 8 and q.lower() not in {cleaned.lower()}:
            queries.append(q)
    if cleaned and cleaned not in queries:
        queries.insert(0, cleaned)
    deduped: List[str] = []
    seen = set()
    for q in queries:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(q)
        if len(deduped) >= limit:
            break
    return deduped


def _should_skip_query_grooming(prompt: str) -> bool:
    cleaned = re.sub(r"\s+", " ", prompt or "").strip()
    if not cleaned:
        return True
    if len(cleaned) <= 32 and ',' not in cleaned and ' and ' not in cleaned.lower():
        return True
    words = cleaned.split()
    if 1 <= len(words) <= 5 and all(len(w) >= 3 for w in words):
        return True
    return False


def _groom_queries(prompt: str, limit: int = 3) -> List[str]:
    cleaned = re.sub(r"\s+", " ", prompt or "").strip()
    if not cleaned:
        return []
    if _should_skip_query_grooming(cleaned):
        return _heuristic_queries(cleaned, limit=limit)
    model = os.environ.get("OCMEMOG_PONDER_MODEL", "local-openai:qwen2.5-7b-instruct")
    ask = (
        "Rewrite this raw memory request into up to 3 short search queries. "
        "Return strict JSON as {\"queries\":[\"...\"]}. "
        "Prefer compact entity/topic phrases, not full sentences.\n\n"
        f"Request: {cleaned}\n"
    )
    try:
        result = inference.infer(ask, provider_name=model)
    except Exception:
        return _heuristic_queries(cleaned, limit=limit)
    if result.get("status") != "ok":
        return _heuristic_queries(cleaned, limit=limit)
    output = str(result.get("output") or "").strip()
    try:
        payload = json.loads(output)
        raw_queries = payload.get("queries") or []
        queries = [str(q).strip() for q in raw_queries if str(q).strip()]
    except Exception:
        queries = []
    cleaned_queries: List[str] = []
    seen = set()
    for q in queries:
        key = q.lower()
        if len(q) < 4 or key in seen:
            continue
        seen.add(key)
        cleaned_queries.append(q)
        if len(cleaned_queries) >= limit:
            break
    return cleaned_queries or _heuristic_queries(cleaned, limit=limit)


def build_context(prompt: str, memory_queries: List[str] | None = None, limit: int = 5) -> Dict[str, Any]:
    emit_event(LOGFILE, "brain_memory_context_build_start", status="ok")
    queries = memory_queries or _groom_queries(prompt, limit=3)
    memories: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        for item in retrieval.retrieve_memories(query, limit=limit):
            ref = str(item.get("reference") or item.get("id") or "")
            if ref and ref in seen:
                continue
            if ref:
                seen.add(ref)
            memories.append(item)
            if len(memories) >= limit:
                break
        if len(memories) >= limit:
            break

    emit_event(LOGFILE, "brain_memory_context_build_complete", status="ok", item_count=len(memories), query_count=len(queries))
    return {
        "prompt": prompt,
        "queries": queries,
        "memories": memories,
    }
