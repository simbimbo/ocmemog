from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ocmemog.sidecar import app
from brain.runtime.memory import api, retrieval, store


class GovernanceReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)
        store._SCHEMA_READY = False

    def test_governance_candidates_endpoint_lists_pending_candidates(self) -> None:
        first = api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        with mock.patch(
            "brain.runtime.memory.api._model_contradiction_hint",
            return_value={"contradiction": True, "confidence": 0.9, "rationale": "same subject, different port"},
        ):
            api.store_memory("knowledge", "Gateway should run on port 17890", source="test")

        result = app.memory_governance_candidates(app.GovernanceCandidatesRequest(categories=["knowledge"], limit=20))
        refs = [item["reference"] for item in result["items"]]
        self.assertIn("knowledge:2", refs)
        item = next(entry for entry in result["items"] if entry["reference"] == "knowledge:2")
        self.assertIn(f"knowledge:{first}", item["contradiction_candidates"])

    def test_governance_decision_endpoint_can_promote_duplicate(self) -> None:
        canonical = api.store_memory("knowledge", "FortiGate admin access stays restricted", source="test")
        with mock.patch("brain.runtime.memory.api._model_contradiction_hint", return_value=None):
            duplicate = api.store_memory("knowledge", "FortiGate admin access stays restricted", source="test")

        result = app.memory_governance_decision(
            app.GovernanceDecisionRequest(
                reference=f"knowledge:{duplicate}",
                relationship="duplicate_of",
                target_reference=f"knowledge:{canonical}",
                approved=True,
            )
        )
        self.assertTrue(result["ok"])

        with mock.patch("brain.runtime.memory.vector_index.search_memory", return_value=[]):
            search = retrieval.retrieve("FortiGate admin access", limit=10, categories=["knowledge"])
        refs = [item["memory_reference"] for item in search["knowledge"]]
        self.assertIn(f"knowledge:{canonical}", refs)
        self.assertNotIn(f"knowledge:{duplicate}", refs)


if __name__ == "__main__":
    unittest.main()
