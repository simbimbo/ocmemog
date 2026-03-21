from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ocmemog.runtime.memory import api, promote, provenance, retrieval, store


class PromotionGovernanceIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE"] = "true"
        os.environ["OCMEMOG_GOVERNANCE_DUPLICATE_AUTOPROMOTE_SIMILARITY"] = "0.98"
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        for key in [
            "OCMEMOG_STATE_DIR",
            "OCMEMOG_GOVERNANCE_AUTOPROMOTE",
            "OCMEMOG_GOVERNANCE_DUPLICATE_AUTOPROMOTE_SIMILARITY",
        ]:
            os.environ.pop(key, None)
        store._SCHEMA_READY = False

    def test_promoted_memory_runs_governance_autopromotion(self) -> None:
        canonical = api.store_memory("knowledge", "FortiGate admin access stays restricted", source="seed")
        candidate = {
            "candidate_id": "cand-1",
            "source_event_id": "event-1",
            "distilled_summary": "FortiGate admin access stays restricted",
            "confidence_score": 0.95,
            "metadata": {"source_labels": ["distill"]},
        }
        with mock.patch("ocmemog.runtime.memory.api._model_contradiction_hint", return_value=None):
            result = promote.promote_candidate(candidate)

        self.assertEqual(result["decision"], "promote")

        conn = store.connect()
        try:
            row = conn.execute("SELECT id FROM knowledge ORDER BY id DESC LIMIT 1").fetchone()
            promoted_ref = f"knowledge:{int(row['id'] if isinstance(row, dict) else row[0])}"
        finally:
            conn.close()

        payload = provenance.fetch_reference(promoted_ref) or {}
        prov = (payload.get("metadata") or {}).get("provenance") or {}
        self.assertEqual(prov.get("memory_status"), "duplicate")
        self.assertEqual(prov.get("duplicate_of"), f"knowledge:{canonical}")

        with mock.patch("ocmemog.runtime.memory.vector_index.search_memory", return_value=[]):
            search = retrieval.retrieve("FortiGate admin access", limit=10, categories=["knowledge"])
        refs = [item["memory_reference"] for item in search["knowledge"]]
        self.assertIn(f"knowledge:{canonical}", refs)
        self.assertNotIn(promoted_ref, refs)


if __name__ == "__main__":
    unittest.main()
