#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from urllib import request as urlrequest

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ocmemog.runtime.memory import store

ENDPOINT = "http://127.0.0.1:17891"


def post(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(f"{ENDPOINT}{path}", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urlrequest.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(path: str) -> dict:
    with urlrequest.urlopen(f"{ENDPOINT}{path}", timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def demo_context_anchor() -> dict:
    conn = store.connect()
    row = conn.execute(
        "SELECT source_reference, target_reference FROM memory_links WHERE link_type='transcript' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return {"ok": False, "error": "no_transcript_links"}
    reference = row[0]
    context = post("/memory/context", {"reference": reference, "radius": 5})
    return {"reference": reference, "context": context}


def demo_precision() -> dict:
    queries = [
        "ssh key policy",
        "synology nas",
        "openclaw status --deep",
        "gateway bind loopback",
        "llama.cpp embeddings",
        "memory pipeline",
        "jira projects",
        "calix arden",
    ]
    results = []
    for query in queries:
        t0 = time.time()
        resp = post("/memory/search", {"query": query, "limit": 5})
        elapsed = time.time() - t0
        hits = 0
        top = resp.get("results", []) or []
        for item in top:
            if any(token in str(item.get("content", "")).lower() for token in query.split()):
                hits += 1
        results.append({
            "query": query,
            "hits": hits,
            "elapsed": round(elapsed, 3),
            "top": [str(item.get("content", ""))[:160] for item in top[:2]],
        })
    hit_rate = round(sum(1 for r in results if r["hits"] > 0) / max(1, len(results)), 3)
    return {"hit_rate": hit_rate, "samples": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    metrics = get("/metrics")
    cold_count = 0
    try:
        conn = store.connect()
        cold_count = conn.execute("SELECT COUNT(*) FROM cold_storage").fetchone()[0]
        conn.close()
    except Exception:
        cold_count = 0

    anchor = demo_context_anchor()
    precision = demo_precision()

    if args.pretty:
        counts = metrics["metrics"]["counts"]
        print("=== ocmemog demo (pretty) ===")
        print(f"Memories: {counts}")
        print(f"Cold storage: {cold_count}")
        if anchor.get("context", {}).get("transcript", {}).get("snippet"):
            snippet = anchor["context"]["transcript"]["snippet"].splitlines()[:5]
            print("\nContext anchor snippet:")
            for line in snippet:
                print(f"  {line}")
        print("\nSearch quality (hit‑rate):", precision.get("hit_rate"))
        for sample in precision.get("samples", [])[:6]:
            print(f"  - {sample['query']} → hits: {sample['hits']} (top: {sample['top'][0] if sample.get('top') else ''})")
        return

    print("=== ocmemog demo ===")
    print(f"Memory counts: {metrics['metrics']['counts']}")
    print(f"Cold storage count: {cold_count}")

    print("\n--- Context anchor demo ---")
    print(json.dumps(anchor, indent=2)[:1000])

    print("\n--- Precision@5 sample ---")
    print(json.dumps(precision, indent=2)[:1000])


if __name__ == "__main__":
    main()
