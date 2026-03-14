#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from brain.runtime.memory import vector_index

if __name__ == "__main__":
    count = vector_index.rebuild_vector_index()
    print(f"reindexed: {count}")
