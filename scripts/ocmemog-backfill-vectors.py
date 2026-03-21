#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("OCMEMOG_USE_OLLAMA", "false")
os.environ.setdefault("OCMEMOG_LOCAL_LLM_BASE_URL", "http://127.0.0.1:18080/v1")
os.environ.setdefault("OCMEMOG_LOCAL_LLM_MODEL", "qwen2.5-7b-instruct")
os.environ.setdefault("OCMEMOG_LOCAL_EMBED_BASE_URL", "http://127.0.0.1:18081/v1")
os.environ.setdefault("OCMEMOG_LOCAL_EMBED_MODEL", "nomic-embed-text-v1.5")
os.environ.setdefault("OCMEMOG_EMBED_MODEL_PROVIDER", "local-openai")
os.environ.setdefault("OCMEMOG_EMBED_MODEL_LOCAL", "")
os.environ.setdefault("BRAIN_EMBED_MODEL_PROVIDER", os.environ["OCMEMOG_EMBED_MODEL_PROVIDER"])
os.environ.setdefault("BRAIN_EMBED_MODEL_LOCAL", os.environ["OCMEMOG_EMBED_MODEL_LOCAL"])
os.environ.setdefault("OCMEMOG_STATE_DIR", str(REPO_ROOT / ".ocmemog-state"))

from ocmemog.runtime.memory import vector_index


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill missing vector embeddings without clearing existing ones")
    parser.add_argument("--table", dest="tables", action="append", help="Table to backfill (repeatable)")
    parser.add_argument("--limit-per-table", type=int, default=None, help="Optional max missing rows per table")
    args = parser.parse_args()
    count = vector_index.backfill_missing_vectors(tables=args.tables, limit_per_table=args.limit_per_table)
    print(f"backfilled: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
