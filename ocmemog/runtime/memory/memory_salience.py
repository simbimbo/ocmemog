from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from ocmemog.runtime import state_store
from ocmemog.runtime.instrumentation import emit_event
from ocmemog.runtime.memory import freshness

LOGFILE = state_store.report_log_path()


def score_salience(record: Mapping[str, float]) -> Dict[str, float | bool]:
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


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _content_text(record: Mapping[str, Any]) -> str:
    return str(record.get("effective_content") or record.get("content") or "").strip()


def _normalized_position(record_id: int, *, latest_id: int, earliest_id: int) -> float:
    if latest_id <= earliest_id:
        return 1.0
    return max(0.0, min(1.0, (record_id - earliest_id) / float(latest_id - earliest_id)))


def score_turn_salience(
    turn: Mapping[str, Any],
    *,
    latest_turn_id: int | None = None,
    earliest_turn_id: int | None = None,
    active_branch_id: str | None = None,
    reply_chain_turn_ids: Sequence[int] | None = None,
) -> Dict[str, Any]:
    metadata = turn.get("metadata") if isinstance(turn.get("metadata"), dict) else {}
    resolution = metadata.get("resolution") if isinstance(metadata.get("resolution"), dict) else {}
    role = str(turn.get("role") or "")
    content = _content_text(turn)
    turn_id = int(turn.get("id") or 0)
    branch_id = str(metadata.get("branch_id") or "")
    latest_turn_id = int(latest_turn_id or turn_id or 1)
    earliest_turn_id = int(earliest_turn_id or turn_id or 1)
    reply_chain_ids = {int(item) for item in (reply_chain_turn_ids or []) if int(item or 0) > 0}
    freshness_score = _normalized_position(turn_id or earliest_turn_id, latest_id=latest_turn_id, earliest_id=earliest_turn_id)

    importance = 0.55 if role == "user" else 0.35
    novelty = 0.25 if metadata.get("branch_depth") == 0 and branch_id else 0.1
    uncertainty = 0.45 if "?" in content else 0.0
    risk = 0.35 if resolution.get("decision") == "decline" else 0.0
    goal_alignment = 0.45 if role == "user" else 0.0
    if resolution:
        goal_alignment += 0.2
    if any(token in content.lower() for token in ("i will", "i'll", "let me", "next", "need to", "please", "can you")):
        goal_alignment += 0.2
    reinforcement = 0.2 if resolution.get("decision") == "confirm" else 0.0
    user_interest = 0.3 if role == "user" else 0.0
    signal_priority = 0.0
    if active_branch_id and branch_id and branch_id == active_branch_id:
        signal_priority += 0.45
    if turn_id and turn_id in reply_chain_ids:
        signal_priority += 0.35
    if metadata.get("reply_to_turn_id"):
        signal_priority += 0.1

    scored = score_salience(
        {
            "importance": importance,
            "novelty": novelty,
            "uncertainty": uncertainty,
            "risk": risk,
            "goal_alignment": min(goal_alignment, 0.8),
            "reinforcement": reinforcement,
            "user_interest": user_interest,
            "freshness": freshness_score,
            "signal_priority": min(signal_priority, 0.9),
        }
    )
    return {
        **dict(scored),
        "reference": turn.get("reference"),
        "id": turn.get("id"),
        "role": role,
        "content": content,
        "branch_id": branch_id or None,
        "resolution": resolution or None,
    }


def score_checkpoint_salience(
    checkpoint: Mapping[str, Any],
    *,
    latest_checkpoint_id: int | None = None,
    active_branch_id: str | None = None,
) -> Dict[str, Any]:
    metadata = checkpoint.get("metadata") if isinstance(checkpoint.get("metadata"), dict) else {}
    active_branch = metadata.get("active_branch") if isinstance(metadata.get("active_branch"), dict) else {}
    checkpoint_id = int(checkpoint.get("id") or 0)
    latest_checkpoint_id = int(latest_checkpoint_id or checkpoint_id or 1)
    freshness_score = 1.0 if latest_checkpoint_id <= 0 else max(0.0, min(1.0, checkpoint_id / float(latest_checkpoint_id or 1)))
    open_loops = checkpoint.get("open_loops") if isinstance(checkpoint.get("open_loops"), list) else []
    pending_actions = checkpoint.get("pending_actions") if isinstance(checkpoint.get("pending_actions"), list) else []
    latest_user_ask = str(checkpoint.get("latest_user_ask") or "").strip()
    commitment = str(checkpoint.get("last_assistant_commitment") or "").strip()

    importance = 0.45 + min(0.3, len(open_loops) * 0.08)
    novelty = 0.1 + (0.15 if int(checkpoint.get("depth") or 0) == 0 else 0.0)
    uncertainty = 0.3 if "?" in latest_user_ask else 0.0
    risk = min(0.45, len(pending_actions) * 0.08)
    goal_alignment = 0.25 + (0.2 if latest_user_ask else 0.0) + (0.15 if commitment else 0.0)
    reinforcement = 0.0
    user_interest = 0.25 if latest_user_ask else 0.0
    signal_priority = 0.0
    if active_branch_id and str(active_branch.get("branch_id") or "") == active_branch_id:
        signal_priority += 0.35
    if open_loops:
        signal_priority += 0.25

    scored = score_salience(
        {
            "importance": min(importance, 0.8),
            "novelty": novelty,
            "uncertainty": uncertainty,
            "risk": risk,
            "goal_alignment": min(goal_alignment, 0.8),
            "reinforcement": reinforcement,
            "user_interest": user_interest,
            "freshness": freshness_score,
            "signal_priority": min(signal_priority, 0.8),
        }
    )
    return {
        **dict(scored),
        "reference": checkpoint.get("reference"),
        "id": checkpoint.get("id"),
        "summary": str(checkpoint.get("summary") or "").strip(),
        "active_branch_id": active_branch.get("branch_id"),
    }


def rank_turns_by_salience(
    turns: Sequence[Mapping[str, Any]],
    *,
    active_branch_id: str | None = None,
    reply_chain_turn_ids: Sequence[int] | None = None,
    limit: int | None = None,
) -> List[Dict[str, Any]]:
    turns_list = list(turns)
    if not turns_list:
        return []
    ids = [int(item.get("id") or 0) for item in turns_list if int(item.get("id") or 0) > 0]
    latest_turn_id = max(ids) if ids else 1
    earliest_turn_id = min(ids) if ids else latest_turn_id
    ranked = []
    for turn in turns_list:
        salience = score_turn_salience(
            turn,
            latest_turn_id=latest_turn_id,
            earliest_turn_id=earliest_turn_id,
            active_branch_id=active_branch_id,
            reply_chain_turn_ids=reply_chain_turn_ids,
        )
        ranked.append({"turn": dict(turn), "salience": salience})
    ranked.sort(
        key=lambda item: (
            _as_float(item["salience"].get("salience_score")),
            _as_float((item["turn"] or {}).get("id")),
        ),
        reverse=True,
    )
    return ranked[: limit or len(ranked)]


def rank_checkpoints_by_salience(
    checkpoints: Sequence[Mapping[str, Any]],
    *,
    active_branch_id: str | None = None,
    limit: int | None = None,
) -> List[Dict[str, Any]]:
    checkpoint_list = list(checkpoints)
    if not checkpoint_list:
        return []
    latest_checkpoint_id = max(int(item.get("id") or 0) for item in checkpoint_list) or 1
    ranked = []
    for checkpoint in checkpoint_list:
        salience = score_checkpoint_salience(
            checkpoint,
            latest_checkpoint_id=latest_checkpoint_id,
            active_branch_id=active_branch_id,
        )
        ranked.append({"checkpoint": dict(checkpoint), "salience": salience})
    ranked.sort(
        key=lambda item: (
            _as_float(item["salience"].get("salience_score")),
            _as_float((item["checkpoint"] or {}).get("id")),
        ),
        reverse=True,
    )
    return ranked[: limit or len(ranked)]


def scan_salient_memories(limit: int = 5) -> List[Dict[str, float | bool]]:
    advisories = freshness.scan_freshness(limit=limit).get("advisories", [])
    results = []
    for item in advisories:
        score = score_salience({"freshness": float(item.get("freshness_score", 0.0))})
        if score.get("attention_trigger"):
            results.append(score)
    return results[:limit]
