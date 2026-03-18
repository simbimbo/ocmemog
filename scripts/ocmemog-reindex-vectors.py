#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("OCMEMOG_USE_OLLAMA", "true")
os.environ.setdefault("OCMEMOG_OLLAMA_MODEL", "phi3:latest")
os.environ.setdefault("OCMEMOG_OLLAMA_EMBED_MODEL", "nomic-embed-text:latest")
os.environ.setdefault("BRAIN_EMBED_MODEL_PROVIDER", "ollama")
os.environ.setdefault("BRAIN_EMBED_MODEL_LOCAL", "")
os.environ.setdefault("OCMEMOG_STATE_DIR", str(REPO_ROOT / ".ocmemog-state"))

from brain.runtime.memory import vector_index

if __name__ == "__main__":
    count = vector_index.rebuild_vector_index()
    print(f"reindexed: {count}")
