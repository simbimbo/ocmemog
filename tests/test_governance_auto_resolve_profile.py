from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ocmemog.sidecar import app
from brain.runtime.memory import api, store


class GovernanceAutoResolveProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE"] = "false"
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE_SUPERSESSION"] = "false"
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        for key in [
            "OCMEMOG_STATE_DIR",
            "OCMEMOG_GOVERNANCE_AUTOPROMOTE",
            "OCMEMOG_GOVERNANCE_AUTOPROMOTE_SUPERSESSION",
            "OCMEMOG_GOVERNANCE_AUTORESOLVE_PROFILE",
        ]:
            os.environ.pop(key, None)
        store._SCHEMA_READY = False

    def test_profile_overrides_defaults(self) -> None:
        api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        with mock.patch(
            "brain.runtime.memory.api._model_contradiction_hint",
            return_value={"contradiction": True, "confidence": 0.99, "rationale": "same subject different port"},
        ):
            api.store_memory("knowledge", "Gateway should run on port 17890", source="test")

        result = app.memory_governance_auto_resolve(
            app.GovernanceAutoResolveRequest(categories=["knowledge"], limit=20, dry_run=True, profile="aggressive")
        )
        policy = result["result"]["policy"]
        self.assertEqual(policy["profile"], "aggressive")
        self.assertGreaterEqual(policy["max_apply"], 20)
        self.assertLessEqual(policy["min_supersession_signal"], 0.9)


if __name__ == "__main__":
    unittest.main()
