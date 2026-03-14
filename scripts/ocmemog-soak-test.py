#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import subprocess


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=3600)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--mode", default="mixed", choices=["search", "ingest", "mixed"])
    parser.add_argument("--out", default="/Users/simbimbo/ocmemog/reports/load/soak-latest.jsonl")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    start = time.time()
    while time.time() - start < args.duration:
        cmd = [
            "/Users/simbimbo/ocmemog/scripts/ocmemog-load-test.py",
            "--mode", args.mode,
            "--duration", str(args.interval),
            "--concurrency", str(args.concurrency),
        ]
        result = subprocess.check_output(cmd).decode("utf-8").strip()
        data = json.loads(result)
        data["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with out_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data) + "\n")
        time.sleep(1)


if __name__ == "__main__":
    main()
