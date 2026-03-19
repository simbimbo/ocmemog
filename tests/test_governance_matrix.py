from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ocmemog.sidecar import app
from brain.runtime.memory import api, provenance, retrieval, store


class GovernanceMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE"] = "true"
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE_SUPERSESSION"] = "false"
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        for key in [
            "OCMEMOG_STATE_DIR",
            "OCMEMOG_GOVERNANCE_AUTOPROMOTE",
            "OCMEMOG_GOVERNANCE_AUTOPROMOTE_SUPERSESSION",
            "OCMEMOG_GOVERNANCE_AUTORESOLVE_ALLOW_KINDS",
            "OCMEMOG_GOVERNANCE_AUTORESOLVE_MIN_SUPERSESSION_SIGNAL",
        ]:
            os.environ.pop(key, None)
        store._SCHEMA_READY = False

    def test_auto_promotion_blocked_when_contradiction_candidates_exist(self) -> None:
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

    def test_auto_resolve_kind_allowlist_skips_duplicates(self) -> None:
        os.environ["OCMEMOG_GOVERNANCE_AUTORESOLVE_ALLOW_KINDS"] = "supersession_recommendation"
        api.store_memory("knowledge", "FortiGate admin access stays restricted", source="test")
        with mock.patch("brain.runtime.memory.api._model_contradiction_hint", return_value=None):
            api.store_memory("knowledge", "FortiGate admin access stays restricted", source="test")

        result = app.memory_governance_auto_resolve(
            app.GovernanceAutoResolveRequest(categories=["knowledge"], limit=20, dry_run=False)
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["applied"], 0)
        actions = result["result"]["actions"]
        self.assertTrue(any(item["reason"] == "kind_not_allowed" for item in actions))

    def test_contradiction_candidate_penalty_keeps_memory_visible(self) -> None:
        first = api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        second = api.store_memory("knowledge", "Gateway should run on port 17890", source="test")
        api.mark_memory_relationship(f"knowledge:{second}", relationship="contradicts", target_reference=f"knowledge:{first}")

        with mock.patch("brain.runtime.memory.vector_index.search_memory", return_value=[]):
            results = retrieval.retrieve("Gateway port", limit=10, categories=["knowledge"])
        refs = [item["memory_reference"] for item in results["knowledge"]]
        self.assertIn(f"knowledge:{second}", refs)
        contested = next(item for item in results["knowledge"] if item["memory_reference"] == f"knowledge:{second}")
        self.assertEqual(contested["memory_status"], "contested")
        self.assertIn("contradiction_penalty", contested["retrieval_signals"])


if __name__ == "__main__":
    unittest.main()
