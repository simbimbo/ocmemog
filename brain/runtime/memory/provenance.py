from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from ocmemog.runtime.memory import memory_links, store

_MEMORY_TABLES = set(store.MEMORY_TABLES)
_FETCHABLE_TABLES = _MEMORY_TABLES | {"promotions", "experiences", "conversation_turns", "conversation_checkpoints"}
_SYNTHETIC_PREFIXES = {"conversation", "session", "thread", "message", "label", "transcript"}


def _load_json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def _dedupe(values: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    items: List[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        items.append(item)
    return items


def _transcript_target(path: str, start_line: Any = None, end_line: Any = None) -> str:
    suffix = ""
    try:
        start = int(start_line) if start_line is not None else None
    except Exception:
        start = None
    try:
        end = int(end_line) if end_line is not None else None
    except Exception:
        end = None
    if start and end and end >= start:
        suffix = f"#L{start}-L{end}"
    elif start:
        suffix = f"#L{start}"
    return f"transcript:{path}{suffix}"


def normalize_metadata(metadata: Optional[Dict[str, Any]], *, source: Optional[str] = None) -> Dict[str, Any]:
    raw = dict(metadata or {})
    existing = raw.get("provenance") if isinstance(raw.get("provenance"), dict) else {}

    source_references = _dedupe(
        [
            *(existing.get("source_references") or []),
            *(raw.get("source_references") or []),
            existing.get("source_reference") or "",
            raw.get("source_reference") or "",
            existing.get("experience_reference") or "",
            raw.get("experience_reference") or "",
        ]
    )
    source_labels = _dedupe(
        [
            *(existing.get("source_labels") or []),
            *(raw.get("source_labels") or []),
            existing.get("source_label") or "",
            raw.get("source_label") or "",
        ]
    )

    conversation = dict(existing.get("conversation") or {})
    for key in ("conversation_id", "session_id", "thread_id", "message_id", "role"):
        if raw.get(key) is not None and conversation.get(key) is None:
            conversation[key] = raw.get(key)

    transcript_anchor = dict(existing.get("transcript_anchor") or {})
    if raw.get("transcript_path") and not transcript_anchor.get("path"):
        transcript_anchor = {
            "path": raw.get("transcript_path"),
            "start_line": raw.get("transcript_offset"),
            "end_line": raw.get("transcript_end_offset"),
        }

    provenance: Dict[str, Any] = dict(existing)
    if source_references:
        provenance["source_references"] = source_references
        provenance["source_reference"] = source_references[0]
    if source_labels:
        provenance["source_labels"] = source_labels
    if conversation:
        provenance["conversation"] = conversation
    if transcript_anchor.get("path"):
        provenance["transcript_anchor"] = transcript_anchor
    if source:
        provenance.setdefault("origin_source", source)

    for key in (
        "source_event_id",
        "task_id",
        "candidate_id",
        "promotion_id",
        "experience_reference",
        "derived_from_candidate_id",
        "derived_from_promotion_id",
        "derived_via",
        "kind",
        "memory_status",
        "superseded_by",
        "supersedes",
        "duplicate_of",
        "duplicate_candidates",
        "contradicts",
        "contradiction_candidates",
        "contradiction_status",
        "canonical_reference",
        "supersession_recommendation",
    ):
        if raw.get(key) is not None and provenance.get(key) is None:
            provenance[key] = raw.get(key)

    if provenance:
        raw["provenance"] = provenance
    if source_references:
        raw["source_reference"] = source_references[0]
        raw["source_references"] = source_references
    if source_labels:
        raw["source_labels"] = source_labels
        raw.setdefault("source_label", source_labels[0])
    return raw


def preview_from_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    normalized = normalize_metadata(metadata)
    provenance = normalized.get("provenance") if isinstance(normalized.get("provenance"), dict) else {}
    return {
        "source_references": provenance.get("source_references") or [],
        "source_labels": provenance.get("source_labels") or [],
        "conversation": provenance.get("conversation") or {},
        "transcript_anchor": provenance.get("transcript_anchor") or None,
        "origin_source": provenance.get("origin_source"),
        "derived_via": provenance.get("derived_via"),
    }


def _link_once(source_reference: str, link_type: str, target_reference: str) -> None:
    if not source_reference or not target_reference:
        return
    existing = memory_links.get_memory_links(source_reference)
    if any(item.get("link_type") == link_type and item.get("target_reference") == target_reference for item in existing):
        return
    memory_links.add_memory_link(source_reference, link_type, target_reference)


def apply_links(reference: str, metadata: Optional[Dict[str, Any]]) -> None:
    normalized = normalize_metadata(metadata)
    provenance = normalized.get("provenance") if isinstance(normalized.get("provenance"), dict) else {}
    for source_reference in provenance.get("source_references") or []:
        _link_once(reference, "derived_from", str(source_reference))
    for label in provenance.get("source_labels") or []:
        _link_once(reference, "source_label", f"label:{label}")
    conversation = provenance.get("conversation") or {}
    for key, link_type in (
        ("conversation_id", "conversation"),
        ("session_id", "session"),
        ("thread_id", "thread"),
        ("message_id", "message"),
    ):
        value = str(conversation.get(key) or "").strip()
        if value:
            _link_once(reference, link_type, f"{link_type}:{value}")
    transcript = provenance.get("transcript_anchor") or {}
    if transcript.get("path"):
        _link_once(
            reference,
            "transcript",
            _transcript_target(
                str(transcript.get("path")),
                transcript.get("start_line"),
                transcript.get("end_line"),
            ),
        )
    if provenance.get("experience_reference"):
        _link_once(reference, "experience", str(provenance.get("experience_reference")))
    if provenance.get("derived_from_candidate_id"):
        _link_once(reference, "candidate", f"candidate:{provenance['derived_from_candidate_id']}")
    if provenance.get("derived_from_promotion_id"):
        _link_once(reference, "promotion", f"promotions:{provenance['derived_from_promotion_id']}")
    if provenance.get("superseded_by"):
        _link_once(reference, "superseded_by", str(provenance.get("superseded_by")))
    if provenance.get("supersedes"):
        _link_once(reference, "supersedes", str(provenance.get("supersedes")))
    if provenance.get("duplicate_of"):
        _link_once(reference, "duplicate_of", str(provenance.get("duplicate_of")))
    for candidate in provenance.get("duplicate_candidates") or []:
        _link_once(reference, "duplicate_candidate", str(candidate))
    for target in provenance.get("contradicts") or []:
        _link_once(reference, "contradicts", str(target))
    for target in provenance.get("contradiction_candidates") or []:
        _link_once(reference, "contradiction_candidate", str(target))
    if provenance.get("canonical_reference"):
        _link_once(reference, "canonical", str(provenance.get("canonical_reference")))


def update_memory_metadata(reference: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    table, sep, raw_id = reference.partition(":")
    if not sep or table not in _MEMORY_TABLES or not raw_id.isdigit():
        return None
    conn = store.connect()
    try:
        row = conn.execute(f"SELECT metadata_json FROM {table} WHERE id = ?", (int(raw_id),)).fetchone()
        if not row:
            return None
        current = _load_json(row["metadata_json"], {})
        merged = normalize_metadata({**current, **updates})
        conn.execute(
            f"UPDATE {table} SET metadata_json = ? WHERE id = ?",
            (json.dumps(merged, ensure_ascii=False), int(raw_id)),
        )
        conn.commit()
    finally:
        conn.close()
    apply_links(reference, merged)
    return merged


def force_update_memory_metadata(reference: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    table, sep, raw_id = reference.partition(":")
    if not sep or table not in _MEMORY_TABLES or not raw_id.isdigit():
        return None
    conn = store.connect()
    try:
        row = conn.execute(f"SELECT metadata_json FROM {table} WHERE id = ?", (int(raw_id),)).fetchone()
        if not row:
            return None
        current = _load_json(row["metadata_json"], {})
        provenance_meta = current.get("provenance") if isinstance(current.get("provenance"), dict) else {}
        for key, value in updates.items():
            if value is None or value == "":
                provenance_meta.pop(key, None)
            else:
                provenance_meta[key] = value
        current["provenance"] = provenance_meta
        conn.execute(
            f"UPDATE {table} SET metadata_json = ? WHERE id = ?",
            (json.dumps(current, ensure_ascii=False), int(raw_id)),
        )
        conn.commit()
    finally:
        conn.close()
    apply_links(reference, current)
    return current


def fetch_reference(reference: str) -> Optional[Dict[str, Any]]:
    prefix, sep, raw_id = reference.partition(":")
    if not sep or not prefix:
        return None
    if prefix in _SYNTHETIC_PREFIXES:
        payload: Dict[str, Any] = {"reference": reference, "type": prefix, "value": raw_id}
        if prefix == "transcript":
            payload["path"] = raw_id
        return payload
    if prefix == "candidate":
        return {"reference": reference, "type": "candidate", "candidate_id": raw_id}
    if prefix not in _FETCHABLE_TABLES:
        return None

    conn = store.connect()
    try:
        if prefix == "conversation_turns":
            if not raw_id.isdigit():
                return None
            row = conn.execute("SELECT * FROM conversation_turns WHERE id = ?", (int(raw_id),)).fetchone()
            if not row:
                return None
            payload = dict(row)
            payload["reference"] = reference
            payload["table"] = prefix
            payload["id"] = int(raw_id)
            payload["metadata"] = _load_json(payload.pop("metadata_json", "{}"), {})
            return payload
        if prefix == "conversation_checkpoints":
            if not raw_id.isdigit():
                return None
            row = conn.execute("SELECT * FROM conversation_checkpoints WHERE id = ?", (int(raw_id),)).fetchone()
            if not row:
                return None
            payload = dict(row)
            payload["reference"] = reference
            payload["table"] = prefix
            payload["id"] = int(raw_id)
            payload["metadata"] = _load_json(payload.pop("metadata_json", "{}"), {})
            payload["open_loops"] = _load_json(payload.pop("open_loops_json", "[]"), [])
            payload["pending_actions"] = _load_json(payload.pop("pending_actions_json", "[]"), [])
            return payload
        if prefix == "experiences":
            if not raw_id.isdigit():
                return None
            row = conn.execute("SELECT * FROM experiences WHERE id = ?", (int(raw_id),)).fetchone()
            if not row:
                return None
            payload = dict(row)
            payload["reference"] = reference
            payload["table"] = prefix
            payload["id"] = int(raw_id)
            payload["content"] = payload.get("outcome")
            payload["metadata"] = _load_json(payload.pop("metadata_json", "{}"), {})
            return payload
        if prefix == "promotions":
            if not raw_id.isdigit():
                return None
            row = conn.execute("SELECT * FROM promotions WHERE id = ?", (int(raw_id),)).fetchone()
            if not row:
                return None
            payload = dict(row)
            payload["reference"] = reference
            payload["table"] = prefix
            payload["id"] = int(raw_id)
            payload["metadata"] = _load_json(payload.pop("metadata_json", "{}"), {})
            return payload
        if not raw_id.isdigit():
            return None
        row = conn.execute(f"SELECT * FROM {prefix} WHERE id = ?", (int(raw_id),)).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["reference"] = reference
        payload["table"] = prefix
        payload["id"] = int(raw_id)
        payload["metadata"] = _load_json(payload.pop("metadata_json", "{}"), {})
        return payload
    finally:
        conn.close()


def _hydrate_target(reference: str, depth: int, seen: Set[str]) -> Optional[Dict[str, Any]]:
    if reference in seen:
        return {"reference": reference, "cycle": True}
    return hydrate_reference(reference, depth=depth, _seen=seen)


def hydrate_reference(reference: str, *, depth: int = 1, _seen: Optional[Set[str]] = None) -> Optional[Dict[str, Any]]:
    seen = set(_seen or set())
    if reference in seen:
        return {"reference": reference, "cycle": True}
    seen.add(reference)
    payload = fetch_reference(reference)
    if payload is None:
        return None

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    preview = preview_from_metadata(metadata)
    links = memory_links.get_memory_links(reference)
    backlinks = memory_links.get_memory_links_for_target(reference)

    payload["provenance_preview"] = preview
    payload["links"] = links
    payload["backlinks"] = backlinks
    if depth <= 0:
        return payload

    payload["provenance"] = {
        "outbound": [
            {
                **item,
                "target": _hydrate_target(str(item.get("target_reference") or ""), depth - 1, seen),
            }
            for item in links
        ],
        "inbound": [
            {
                **item,
                "source": _hydrate_target(str(item.get("source_reference") or ""), depth - 1, seen),
            }
            for item in backlinks
        ],
    }
    return payload


def collect_source_references(reference: str, *, depth: int = 2) -> List[str]:
    pending = [reference]
    seen: Set[str] = set()
    collected: List[str] = []
    remaining = max(0, int(depth))
    while pending and remaining >= 0:
        next_round: List[str] = []
        for current in pending:
            if current in seen:
                continue
            seen.add(current)
            collected.append(current)
            row = fetch_reference(current)
            metadata = row.get("metadata") if isinstance((row or {}).get("metadata"), dict) else {}
            preview = preview_from_metadata(metadata)
            for source_ref in preview.get("source_references") or []:
                if source_ref not in seen:
                    next_round.append(str(source_ref))
        pending = next_round
        remaining -= 1
    return _dedupe(collected)


def source_references_only(reference: str, *, depth: int = 2) -> List[str]:
    refs = collect_source_references(reference, depth=depth)
    return refs[1:] if len(refs) > 1 else []
