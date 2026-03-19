from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from brain.runtime.memory import api, provenance, retrieval, store


class GovernanceAutopromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE"] = "true"
        os.environ["OCMEMOG_GOVERNANCE_DUPLICATE_AUTOPROMOTE_SIMILARITY"] = "0.92"
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)
        os.environ.pop("OCMEMOG_GOVERNANCE_AUTOPROMOTE", None)
        os.environ.pop("OCMEMOG_GOVERNANCE_DUPLICATE_AUTOPROMOTE_SIMILARITY", None)
        store._SCHEMA_READY = False

    def test_auto_promotes_high_confidence_duplicate(self) -> None:
        canonical = api.store_memory("knowledge", "FortiGate admin access stays restricted", source="test")
        with mock.patch("brain.runtime.memory.api._model_contradiction_hint", return_value=None):
            duplicate = api.store_memory("knowledge", "FortiGate admin access stays restricted", source="test")

        payload = provenance.fetch_reference(f"knowledge:{duplicate}") or {}
        prov = (payload.get("metadata") or {}).get("provenance") or {}
        self.assertEqual(prov.get("memory_status"), "duplicate")
        self.assertEqual(prov.get("duplicate_of"), f"knowledge:{canonical}")

        with mock.patch("brain.runtime.memory.vector_index.search_memory", return_value=[]):
            search = retrieval.retrieve("FortiGate admin access", limit=10, categories=["knowledge"])
        refs = [item["memory_reference"] for item in search["knowledge"]]
        self.assertIn(f"knowledge:{canonical}", refs)
        self.assertNotIn(f"knowledge:{duplicate}", refs)

    def test_does_not_auto_promote_when_contradiction_candidates_exist(self) -> None:
        api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        with mock.patch(
            "brain.runtime.memory.api._model_contradiction_hint",
            return_value={"contradiction": True, "confidence": 0.95, "rationale": "same subject different port"},
        ):
            changed = api.store_memory("knowledge", "Gateway should run on port 17890", source="test")

        payload = provenance.fetch_reference(f"knowledge:{changed}") or {}
        prov = (payload.get("metadata") or {}).get("provenance") or {}
        self.assertNotEqual(prov.get("memory_status"), "duplicate")
        self.assertIn("knowledge:1", prov.get("contradiction_candidates") or [])


if __name__ == "__main__":
    unittest.main()
