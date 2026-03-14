from __future__ import annotations

from typing import Dict, Tuple
import re

from brain.runtime import state_store
from brain.runtime.instrumentation import emit_event
from brain.runtime.memory import person_memory

LOGFILE = state_store.reports_dir() / "brain_memory.log.jsonl"


def extract_intro_candidate(text: str) -> str | None:
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    lowered = cleaned.lower().strip(".!")
    call_me_match = re.search(r"\byou can call me\s+([a-z][a-z\-']*)", lowered)
    if call_me_match:
        return call_me_match.group(1).strip(" .!").title()
    direct_call_match = re.search(r"\bcall me\s+([a-z][a-z\-']*)", lowered)
    if direct_call_match:
        return direct_call_match.group(1).strip(" .!").title()
    patterns = [
        r"^my name is (.+)$",
        r"^i am (.+)$",
        r"^i'm (.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, lowered)
        if match:
            candidate = match.group(1).split(" but ")[0].strip(" .!")
            return candidate.title() if candidate else None
    return None


def extract_name_candidate(text: str) -> str | None:
    candidate = extract_intro_candidate(text)
    if candidate:
        return candidate
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    tokens = [token for token in cleaned.replace(".", "").split() if token]
    if 1 <= len(tokens) <= 3:
        return " ".join(tokens)
    return None


def extract_operator_name(text: str) -> str | None:
    from brain.runtime import inference

    llm_result = inference.parse_operator_name(text)
    llm_name = llm_result.get("name") if isinstance(llm_result, dict) else ""
    if isinstance(llm_name, str) and llm_name.strip():
        return llm_name.strip()
    return extract_name_candidate(text)


def resolve_interaction_person(metadata: Dict[str, str]) -> Tuple[Dict[str, object] | None, float, bool]:
    person = None
    confidence = 0.0
    name_input = metadata.get("name") or ""
    name_candidate = extract_name_candidate(name_input) if name_input else None
    if name_candidate:
        person = person_memory.get_person(name_candidate) or person_memory.create_person(name_candidate, name_candidate)
        confidence = 0.7
    if not person and metadata.get("email"):
        person = person_memory.find_person_by_email(metadata["email"])
        confidence = 0.6 if person else 0.0
    if not person and metadata.get("phone"):
        person = person_memory.find_person_by_phone(metadata["phone"])
        confidence = 0.6 if person else 0.0
    ask_name_required = confidence < 0.5
    if person:
        emit_event(LOGFILE, "brain_person_identity_resolved", status="ok", person_id=person.get("person_id"))
    else:
        emit_event(LOGFILE, "brain_person_identity_uncertain", status="ok")
    return person, confidence, ask_name_required
