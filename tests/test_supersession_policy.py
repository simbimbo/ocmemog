from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ocmemog.runtime.memory import api, provenance, retrieval, store


class SupersessionPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE"] = "true"
        os.environ["OCMEMOG_GOVERNANCE_DUPLICATE_AUTOPROMOTE_SIMILARITY"] = "0.98"
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE_SUPERSESSION"] = "false"
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        for key in [
            "OCMEMOG_STATE_DIR",
            "OCMEMOG_GOVERNANCE_AUTOPROMOTE",
            "OCMEMOG_GOVERNANCE_DUPLICATE_AUTOPROMOTE_SIMILARITY",
            "OCMEMOG_GOVERNANCE_AUTOPROMOTE_SUPERSESSION",
            "OCMEMOG_GOVERNANCE_AUTOPROMOTE_ALLOW_CONTRADICTIONS",
            "OCMEMOG_GOVERNANCE_SUPERSESSION_AUTOPROMOTE_SIGNAL",
            "OCMEMOG_GOVERNANCE_SUPERSESSION_AUTOPROMOTE_MODEL_CONFIDENCE",
        ]:
            os.environ.pop(key, None)
        store._SCHEMA_READY = False

    def test_supersession_is_recommended_not_applied_by_default(self) -> None:
        old_id = api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        with mock.patch(
            "ocmemog.runtime.memory.api._model_contradiction_hint",
            return_value={"contradiction": True, "confidence": 0.95, "rationale": "same subject different port"},
        ):
            new_id = api.store_memory("knowledge", "Gateway should run on port 17890", source="test")

        payload = provenance.fetch_reference(f"knowledge:{new_id}") or {}
        prov = (payload.get("metadata") or {}).get("provenance") or {}
        rec = prov.get("supersession_recommendation") or {}
        self.assertTrue(rec.get("recommended"))
        self.assertEqual(rec.get("target_reference"), f"knowledge:{old_id}")
        self.assertFalse(rec.get("auto_applied"))

    def test_supersession_can_auto_apply_when_enabled(self) -> None:
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE_SUPERSESSION"] = "true"
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE_ALLOW_CONTRADICTIONS"] = "true"
        os.environ["OCMEMOG_GOVERNANCE_SUPERSESSION_AUTOPROMOTE_SIGNAL"] = "0.97"
        os.environ["OCMEMOG_GOVERNANCE_SUPERSESSION_AUTOPROMOTE_MODEL_CONFIDENCE"] = "0.97"
        old_id = api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        with mock.patch(
            "ocmemog.runtime.memory.api._model_contradiction_hint",
            return_value={"contradiction": True, "confidence": 0.99, "rationale": "same subject different port"},
        ):
            new_id = api.store_memory("knowledge", "Gateway should run on port 17890", source="test")

        new_payload = provenance.fetch_reference(f"knowledge:{new_id}") or {}
        new_prov = (new_payload.get("metadata") or {}).get("provenance") or {}
        rec = new_prov.get("supersession_recommendation") or {}
        self.assertTrue(rec.get("auto_applied"))
        self.assertEqual(rec.get("reason"), "auto_applied_supersession")

        old_payload = provenance.fetch_reference(f"knowledge:{old_id}") or {}
        old_prov = (old_payload.get("metadata") or {}).get("provenance") or {}
        self.assertEqual(old_prov.get("memory_status"), "superseded")
        self.assertEqual(old_prov.get("superseded_by"), f"knowledge:{new_id}")

        with mock.patch("ocmemog.runtime.memory.vector_index.search_memory", return_value=[]):
            search = retrieval.retrieve("Gateway port", limit=10, categories=["knowledge"])
        refs = [item["memory_reference"] for item in search["knowledge"]]
        self.assertIn(f"knowledge:{new_id}", refs)
        self.assertNotIn(f"knowledge:{old_id}", refs)


if __name__ == "__main__":
    unittest.main()
