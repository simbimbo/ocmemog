#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib import request as urlrequest

ENDPOINT = "http://127.0.0.1:17890"

QUERIES = [
    "ssh key policy",
    "synology nas",
    "openclaw status --deep",
    "ollama embeddings",
    "memory pipeline",
    "calix arden",
    "gateway bind loopback",
]


def post(path: str, payload: dict, token: str | None = None) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(f"{ENDPOINT}{path}", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("x-ocmemog-token", token)
    with urlrequest.urlopen(req, timeout=30) as resp:
        resp.read()


def run_load(mode: str, duration: int, concurrency: int, token: str | None) -> dict:
    start = time.time()
    latencies = []
    errors = 0
    total = 0

    stop_at = start + duration

    def worker():
        nonlocal errors, total
        while time.time() < stop_at:
            t0 = time.time()
            try:
                if mode == "search":
                    query = random.choice(QUERIES)
                    post("/memory/search", {"query": query, "limit": 5}, token)
                elif mode == "ingest":
                    content = f"load test {random.randint(1, 100000)}"
                    post("/memory/ingest", {"content": content, "kind": "memory", "memory_type": "knowledge"}, token)
                else:
                    if random.random() < 0.7:
                        query = random.choice(QUERIES)
                        post("/memory/search", {"query": query, "limit": 5}, token)
                    else:
                        content = f"load test {random.randint(1, 100000)}"
                        post("/memory/ingest", {"content": content, "kind": "memory", "memory_type": "knowledge"}, token)
            except Exception:
                errors += 1
            else:
                latencies.append(time.time() - t0)
                total += 1

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(worker) for _ in range(concurrency)]
        for f in as_completed(futures):
            pass

    if latencies:
        lat_sorted = sorted(latencies)
        p95 = lat_sorted[int(len(lat_sorted) * 0.95) - 1]
        avg = sum(latencies) / len(latencies)
    else:
        avg = 0
        p95 = 0

    return {
        "mode": mode,
        "duration_s": duration,
        "concurrency": concurrency,
        "requests": total,
        "errors": errors,
        "avg_ms": round(avg * 1000, 2),
        "p95_ms": round(p95 * 1000, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="mixed", choices=["search", "ingest", "mixed"])
    parser.add_argument("--duration", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--token", default="")
    args = parser.parse_args()

    result = run_load(args.mode, args.duration, args.concurrency, args.token or None)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
