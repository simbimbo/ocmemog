"""ocmemog-native security namespace backed by the legacy brain runtime."""

from __future__ import annotations

import importlib
import sys


redaction = importlib.import_module("brain.runtime.security.redaction")
sys.modules.setdefault(__name__ + ".redaction", redaction)

__all__ = ["redaction"]
