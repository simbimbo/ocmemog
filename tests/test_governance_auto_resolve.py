from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ocmemog.sidecar import app
from ocmemog.runtime.memory import api, provenance, retrieval, store


class GovernanceAutoResolveTests(unittest.TestCase):
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

    def test_auto_resolve_dry_run_reports_actions_without_applying(self) -> None:
        api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        with mock.patch(
            "ocmemog.runtime.memory.api._model_contradiction_hint",
            return_value={"contradiction": True, "confidence": 0.99, "rationale": "same subject different port"},
        ):
            api.store_memory("knowledge", "Gateway should run on port 17890", source="test")

        result = app.memory_governance_auto_resolve(
            app.GovernanceAutoResolveRequest(categories=["knowledge"], limit=20, dry_run=True)
        )
        self.assertTrue(result["ok"])
        actions = result["result"]["actions"]
        self.assertGreaterEqual(len(actions), 1)
        self.assertTrue(all(item["dry_run"] for item in actions))
        self.assertIn("autoResolveDiagnostics", result)
        self.assertGreaterEqual(result["autoResolveDiagnostics"]["action_count"], 1)
        self.assertTrue(result["autoResolveDiagnostics"]["dry_run"])
        self.assertIn("dry_run", result["autoResolveDiagnostics"]["reason_counts"])

        old_payload = provenance.fetch_reference("knowledge:1") or {}
        old_prov = (old_payload.get("metadata") or {}).get("provenance") or {}
        self.assertNotEqual(old_prov.get("memory_status"), "superseded")

    def test_auto_resolve_apply_can_promote_supersession(self) -> None:
        api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        with mock.patch(
            "ocmemog.runtime.memory.api._model_contradiction_hint",
            return_value={"contradiction": True, "confidence": 0.99, "rationale": "same subject different port"},
        ):
            api.store_memory("knowledge", "Gateway should run on port 17890", source="test")

        result = app.memory_governance_auto_resolve(
            app.GovernanceAutoResolveRequest(categories=["knowledge"], limit=20, dry_run=False)
        )
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["result"]["applied"], 1)
        self.assertGreaterEqual(result["autoResolveDiagnostics"]["applied_count"], 1)
        self.assertEqual(result["autoResolveDiagnostics"]["policy_profile"], "conservative")

        old_payload = provenance.fetch_reference("knowledge:1") or {}
        old_prov = (old_payload.get("metadata") or {}).get("provenance") or {}
        self.assertEqual(old_prov.get("memory_status"), "superseded")

        with mock.patch("ocmemog.runtime.memory.vector_index.search_memory", return_value=[]):
            search = retrieval.retrieve("Gateway port", limit=10, categories=["knowledge"])
        refs = [item["memory_reference"] for item in search["knowledge"]]
        self.assertIn("knowledge:2", refs)
        self.assertNotIn("knowledge:1", refs)


if __name__ == "__main__":
    unittest.main()
