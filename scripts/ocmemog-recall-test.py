#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import time
from urllib import request as urlrequest

ENDPOINT = "http://127.0.0.1:17890"
QUERIES = [
    "ssh key policy",
    "synology nas",
    "openclaw status --deep",
    "ollama embeddings",
    "memory pipeline",
    "calix arden",
]


def post(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(f"{ENDPOINT}{path}", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urlrequest.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_search() -> dict:
    results = {}
    for q in QUERIES:
        res = post("/memory/search", {"query": q, "limit": 5})
        ids = [r.get("entry_id") for r in (res.get("results") or [])]
        results[q] = ids
    return results


def overlap(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    return len(set(a) & set(b)) / max(1, len(set(a)))


def main() -> None:
    before = run_search()
    import os
    subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.openclaw.ocmemog.sidecar"], check=False)
    time.sleep(2)
    after = run_search()

    rows = []
    for q in QUERIES:
        rows.append({"query": q, "overlap": round(overlap(before[q], after[q]), 3)})

    print(json.dumps({"overlap": rows}, indent=2))


if __name__ == "__main__":
    main()
