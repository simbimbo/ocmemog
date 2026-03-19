from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from brain.runtime.memory import api, provenance, store


class StoreMemoryGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)
        store._SCHEMA_READY = False

    def test_store_memory_auto_attaches_duplicate_candidates(self) -> None:
        first = api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        with mock.patch("brain.runtime.memory.api._model_contradiction_hint", return_value=None):
            second = api.store_memory("knowledge", "Gateway should run on port 18789", source="test")

        payload = provenance.fetch_reference(f"knowledge:{second}") or {}
        prov = (payload.get("metadata") or {}).get("provenance") or {}
        self.assertIn(f"knowledge:{first}", prov.get("duplicate_candidates") or [])

    def test_store_memory_auto_attaches_contradiction_candidates(self) -> None:
        first = api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        with mock.patch(
            "brain.runtime.memory.api._model_contradiction_hint",
            return_value={"contradiction": True, "confidence": 0.9, "rationale": "same subject, different port"},
        ):
            second = api.store_memory("knowledge", "Gateway should run on port 17890", source="test")

        payload = provenance.fetch_reference(f"knowledge:{second}") or {}
        prov = (payload.get("metadata") or {}).get("provenance") or {}
        self.assertIn(f"knowledge:{first}", prov.get("contradicts") or [])
        self.assertIn(f"knowledge:{first}", prov.get("contradiction_candidates") or [])


if __name__ == "__main__":
    unittest.main()
