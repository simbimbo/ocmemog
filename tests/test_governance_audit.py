from __future__ import annotations

import os
import tempfile
import unittest

from ocmemog.sidecar import app
from ocmemog.runtime import state_store
from ocmemog.runtime.instrumentation import emit_event
from ocmemog.runtime.memory import store


class GovernanceAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)
        store._SCHEMA_READY = False

    def test_governance_audit_returns_recent_events(self) -> None:
        log = state_store.reports_dir() / "brain_memory.log.jsonl"
        emit_event(log, "store_memory_governance_candidates", status="ok", reference="knowledge:1")
        emit_event(log, "governance_auto_resolve", status="ok", applied=1)

        result = app.memory_governance_audit(app.GovernanceAuditRequest(limit=10))
        self.assertTrue(result["ok"])
        events = [item.get("event") for item in result["items"]]
        self.assertIn("store_memory_governance_candidates", events)
        self.assertIn("governance_auto_resolve", events)


if __name__ == "__main__":
    unittest.main()
