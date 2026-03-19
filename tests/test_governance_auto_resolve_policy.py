from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ocmemog.sidecar import app
from brain.runtime.memory import api, provenance, store


class GovernanceAutoResolvePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE"] = "false"
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE_SUPERSESSION"] = "false"
        os.environ["OCMEMOG_GOVERNANCE_AUTORESOLVE_MAX_APPLY"] = "1"
        os.environ["OCMEMOG_GOVERNANCE_AUTORESOLVE_ALLOW_KINDS"] = "supersession_recommendation"
        os.environ["OCMEMOG_GOVERNANCE_AUTORESOLVE_MIN_SUPERSESSION_SIGNAL"] = "0.95"
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        for key in [
            "OCMEMOG_STATE_DIR",
            "OCMEMOG_GOVERNANCE_AUTOPROMOTE",
            "OCMEMOG_GOVERNANCE_AUTOPROMOTE_SUPERSESSION",
            "OCMEMOG_GOVERNANCE_AUTORESOLVE_MAX_APPLY",
            "OCMEMOG_GOVERNANCE_AUTORESOLVE_ALLOW_KINDS",
            "OCMEMOG_GOVERNANCE_AUTORESOLVE_MIN_SUPERSESSION_SIGNAL",
            "OCMEMOG_GOVERNANCE_AUTORESOLVE_ALLOW_BUCKETS",
        ]:
            os.environ.pop(key, None)
        store._SCHEMA_READY = False

    def test_auto_resolve_respects_max_apply_and_bucket_allowlist(self) -> None:
        # Create two separate supersession recommendations
        api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        with mock.patch("brain.runtime.memory.api._model_contradiction_hint", return_value={"contradiction": True, "confidence": 0.99, "rationale": "same subject different port"}):
            api.store_memory("knowledge", "Gateway should run on port 17890", source="test")

        api.store_memory("runbooks", "Procedure version should be v1", source="test")
        with mock.patch("brain.runtime.memory.api._model_contradiction_hint", return_value={"contradiction": True, "confidence": 0.99, "rationale": "same subject different version"}):
            api.store_memory("runbooks", "Procedure version should be v2", source="test")

        os.environ["OCMEMOG_GOVERNANCE_AUTORESOLVE_ALLOW_BUCKETS"] = "knowledge"
        result = app.memory_governance_auto_resolve(
            app.GovernanceAutoResolveRequest(categories=["knowledge", "runbooks"], limit=50, dry_run=False)
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["applied"], 1)
        self.assertGreaterEqual(result["result"]["skipped"], 1)

        # Knowledge old entry should be superseded
        k1 = provenance.fetch_reference("knowledge:1") or {}
        k1p = (k1.get("metadata") or {}).get("provenance") or {}
        self.assertEqual(k1p.get("memory_status"), "superseded")

        # Runbooks old entry should not be superseded due to bucket allowlist
        r1 = provenance.fetch_reference("runbooks:1") or {}
        r1p = (r1.get("metadata") or {}).get("provenance") or {}
        self.assertNotEqual(r1p.get("memory_status"), "superseded")


if __name__ == "__main__":
    unittest.main()
