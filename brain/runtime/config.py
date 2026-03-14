from __future__ import annotations

import os

BRAIN_EMBED_MODEL_LOCAL = os.environ.get("BRAIN_EMBED_MODEL_LOCAL", "simple")
BRAIN_EMBED_MODEL_PROVIDER = os.environ.get("BRAIN_EMBED_MODEL_PROVIDER", "")

OCMEMOG_MEMORY_MODEL = os.environ.get("OCMEMOG_MEMORY_MODEL", "gpt-4o-mini")
OCMEMOG_OPENAI_API_BASE = os.environ.get("OCMEMOG_OPENAI_API_BASE", "https://api.openai.com/v1")
OCMEMOG_OPENAI_EMBED_MODEL = os.environ.get("OCMEMOG_OPENAI_EMBED_MODEL", "text-embedding-3-small")
