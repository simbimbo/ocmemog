from __future__ import annotations

from typing import Dict, List, Callable, Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from brain.runtime import state_store, config, inference
from brain.runtime.instrumentation import emit_event
from brain.runtime.memory import unresolved_state, memory_consolidation, memory_links, store, integrity, vector_index, api

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def _run_with_timeout(name: str, fn: Callable[[], Any], timeout_s: float, default: Any) -> Any:
    emit_event(LOGFILE, f"brain_ponder_{name}_start", status="ok")
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn)
        try:
            result = future.result(timeout=timeout_s)
            emit_event(LOGFILE, f"brain_ponder_{name}_complete", status="ok")
            return result
        except TimeoutError:
            emit_event(LOGFILE, f"brain_ponder_{name}_complete", status="timeout")
            return default
        except Exception as exc:  # pragma: no cover
            emit_event(LOGFILE, f"brain_ponder_{name}_complete", status="error", error=str(exc))
            return default


def _infer_with_timeout(prompt: str, timeout_s: float = 20.0) -> Dict[str, str]:
    def _call():
        return inference.infer(prompt, provider_name=config.OCMEMOG_PONDER_MODEL)
    return _run_with_timeout("infer", _call, timeout_s, {"output": ""})


def _load_recent(table: str, limit: int) -> List[Dict[str, object]]:
    conn = store.connect(ensure_schema=False)
    try:
        rows = conn.execute(
            f"SELECT id, content, confidence, timestamp FROM {table} ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except Exception:
        rows = []
    conn.close()
    return [
        {
            "reference": f"{table}:{row['id']}",
            "content": str(row["content"] or ""),
            "confidence": float(row["confidence"] or 0.0),
            "timestamp": row["timestamp"],
        }
        for row in rows
    ]


def _ponder_with_model(text: str) -> Dict[str, str]:
    prompt = (
        "You are the memory pondering engine.\n"
        "Given this memory, return: (1) a concise insight, (2) a concrete recommendation.\n\n"
        f"Memory: {text}\n\n"
        "Format:\nInsight: ...\nRecommendation: ..."
    )
    result = _infer_with_timeout(prompt)
    output = str(result.get("output") or "").strip()
    insight = ""
    recommendation = ""
    for line in output.splitlines():
        if line.lower().startswith("insight:"):
            insight = line.split(":", 1)[-1].strip()
        if line.lower().startswith("recommendation:"):
            recommendation = line.split(":", 1)[-1].strip()
    if not insight:
        insight = output[:240]
    return {"insight": insight, "recommendation": recommendation}


def _extract_lesson(text: str) -> str | None:
    prompt = (
        "Extract a single actionable lesson learned from this memory.\n"
        "If there is no clear lesson, reply with NONE.\n\n"
        f"Memory: {text}\n\n"
        "Lesson:"
    )
    result = _infer_with_timeout(prompt)
    output = str(result.get("output") or "").strip()
    if not output or output.upper().startswith("NONE"):
        return None
    return output[:240]


def run_ponder_cycle(max_items: int = 5) -> Dict[str, object]:
    emit_event(LOGFILE, "brain_ponder_cycle_start", status="ok")

    unresolved = _run_with_timeout(
        "unresolved",
        lambda: unresolved_state.list_unresolved_state(limit=max_items),
        5.0,
        [],
    )
    consolidation = _run_with_timeout(
        "consolidation",
        lambda: memory_consolidation.consolidate_memories([], max_clusters=max_items),
        15.0,
        {"consolidated": []},
    )

    # candidate memories
    candidates = _load_recent("reflections", max_items) + _load_recent("knowledge", max_items)
    candidates = candidates[:max_items]

    insights: List[Dict[str, object]] = []
    for item in unresolved[:max_items]:
        insights.append({"type": "unresolved", "summary": item.get("summary", "")})
        emit_event(LOGFILE, "brain_ponder_insight_generated", status="ok", kind="unresolved")

    if str(config.OCMEMOG_PONDER_ENABLED).lower() in {"1", "true", "yes"}:
        for item in candidates:
            content = str(item.get("content", ""))
            if not content:
                continue
            model_result = _ponder_with_model(content)
            insights.append({
                "type": "memory",
                "reference": item.get("reference"),
                "summary": model_result.get("insight"),
                "recommendation": model_result.get("recommendation"),
            })
            emit_event(LOGFILE, "brain_ponder_insight_generated", status="ok", kind="memory")

    # lesson mining
    lessons: List[Dict[str, object]] = []
    if str(config.OCMEMOG_LESSON_MINING_ENABLED).lower() in {"1", "true", "yes"}:
        for item in candidates:
            reference = str(item.get("reference") or "")
            if not reference.startswith("reflections:"):
                continue
            content = str(item.get("content", ""))
            lesson = _extract_lesson(content)
            if not lesson:
                continue
            api.store_memory("lessons", lesson, source="ponder", metadata={"reference": reference})
            lessons.append({"reference": reference, "lesson": lesson})
            emit_event(LOGFILE, "brain_ponder_lesson_generated", status="ok")

    links: List[Dict[str, object]] = []
    for cluster in consolidation.get("consolidated", []):
        links.append({"type": "cluster", "summary": cluster.get("summary")})
        memory_links.add_memory_link("ponder", "conceptual", str(cluster.get("summary")))

    # maintenance: integrity + vector rebuild on demand
    maintenance = _run_with_timeout(
        "integrity",
        integrity.run_integrity_check,
        10.0,
        {"issues": []},
    )
    if any(item.startswith("vector_missing") or item.startswith("vector_orphan") for item in maintenance.get("issues", [])):
        rebuild_count = _run_with_timeout(
            "vector_rebuild",
            vector_index.rebuild_vector_index,
            30.0,
            0,
        )
        maintenance["vector_rebuild"] = rebuild_count

    emit_event(LOGFILE, "brain_ponder_cycle_complete", status="ok")
    return {
        "unresolved": unresolved,
        "insights": insights,
        "lessons": lessons,
        "links": links,
        "maintenance": maintenance,
    }
