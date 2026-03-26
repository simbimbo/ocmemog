from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ocmemog.sidecar import app
from ocmemog.runtime.memory import api, store


class GovernanceQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE"] = "false"
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE_SUPERSESSION"] = "false"
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        for key in ["OCMEMOG_STATE_DIR", "OCMEMOG_GOVERNANCE_AUTOPROMOTE", "OCMEMOG_GOVERNANCE_AUTOPROMOTE_SUPERSESSION"]:
            os.environ.pop(key, None)
        store._SCHEMA_READY = False

    def test_governance_queue_returns_prioritized_action_items(self) -> None:
        api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        with mock.patch(
            "ocmemog.runtime.memory.api._model_contradiction_hint",
            return_value={"contradiction": True, "confidence": 0.99, "rationale": "same subject different port"},
        ):
            api.store_memory("knowledge", "Gateway should run on port 17890", source="test")

        result = app.memory_governance_queue(app.GovernanceQueueRequest(categories=["knowledge"], limit=50))
        self.assertTrue(result["ok"])
        items = result["items"]
        self.assertGreaterEqual(len(items), 1)
        kinds = [item["kind"] for item in items]
        self.assertIn("contradiction_candidate", kinds)
        self.assertIn("supersession_recommendation", kinds)
        # priority ordering should place supersession recommendations first
        self.assertEqual(items[0]["kind"], "supersession_recommendation")
        self.assertIn("queueDiagnostics", result)
        self.assertGreaterEqual(result["queueDiagnostics"]["item_count"], 1)
        self.assertIn("knowledge", result["queueDiagnostics"]["bucket_counts"])
        self.assertIn("critical", result["queueDiagnostics"]["priority_label_counts"])
        self.assertIn("explanation", items[0])
        self.assertIn("short", items[0]["explanation"])
        self.assertEqual(items[0]["explanation"]["target_reference"], items[0]["target_reference"])


if __name__ == "__main__":
    unittest.main()
