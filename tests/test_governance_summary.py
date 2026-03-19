from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ocmemog.sidecar import app
from brain.runtime.memory import api, store


class GovernanceSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE"] = "false"
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)
        os.environ.pop("OCMEMOG_GOVERNANCE_AUTOPROMOTE", None)
        store._SCHEMA_READY = False

    def test_governance_summary_reports_pending_and_status_counts(self) -> None:
        api.store_memory("knowledge", "FortiGate admin access stays restricted", source="test")
        with mock.patch(
            "brain.runtime.memory.api._model_contradiction_hint",
            return_value={"contradiction": True, "confidence": 0.95, "rationale": "same subject different port"},
        ):
            api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
            api.store_memory("knowledge", "Gateway should run on port 17890", source="test")

        result = app.memory_governance_summary(app.GovernanceSummaryRequest(categories=["knowledge"]))
        self.assertTrue(result["ok"])
        table = result["summary"]["tables"]["knowledge"]
        totals = result["summary"]["totals"]

        self.assertGreaterEqual(table["rows"], 3)
        self.assertGreaterEqual(table["pending_duplicates"], 0)
        self.assertGreaterEqual(table["pending_contradictions"], 1)
        self.assertGreaterEqual(table["recommended_supersessions"], 1)
        self.assertEqual(table["rows"], totals["rows"])


if __name__ == "__main__":
    unittest.main()
