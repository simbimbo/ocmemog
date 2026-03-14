from __future__ import annotations

import re

__shim__ = True


def infer(*_args, **_kwargs):
    raise RuntimeError(
        "TODO: brAIn inference runtime is not bundled in ocmemog yet. "
        "Install the original inference stack or replace this shim."
    )


def parse_operator_name(text: str) -> dict[str, str] | None:
    match = re.search(r"\bmy name is ([A-Z][a-z]+)\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    return {"name": match.group(1)}
