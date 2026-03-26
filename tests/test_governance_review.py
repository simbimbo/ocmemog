from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ocmemog.sidecar import app
from ocmemog.runtime.memory import api, provenance, retrieval, store


class GovernanceReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE"] = "false"
        os.environ["OCMEMOG_GOVERNANCE_AUTOPROMOTE_SUPERSESSION"] = "false"
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)
        os.environ.pop("OCMEMOG_GOVERNANCE_AUTOPROMOTE", None)
        os.environ.pop("OCMEMOG_GOVERNANCE_AUTOPROMOTE_SUPERSESSION", None)
        store._SCHEMA_READY = False

    def test_governance_candidates_endpoint_lists_pending_candidates(self) -> None:
        first = api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        with mock.patch(
            "ocmemog.runtime.memory.api._model_contradiction_hint",
            return_value={"contradiction": True, "confidence": 0.9, "rationale": "same subject, different port"},
        ):
            api.store_memory("knowledge", "Gateway should run on port 17890", source="test")

        result = app.memory_governance_candidates(app.GovernanceCandidatesRequest(categories=["knowledge"], limit=20))
        refs = [item["reference"] for item in result["items"]]
        self.assertIn("knowledge:2", refs)
        item = next(entry for entry in result["items"] if entry["reference"] == "knowledge:2")
        self.assertIn(f"knowledge:{first}", item["contradiction_candidates"])

    def test_governance_review_endpoint_lists_pending_items_with_context(self) -> None:
        canonical = api.store_memory("knowledge", "FortiGate admin access stays restricted", source="test")
        with mock.patch("ocmemog.runtime.memory.api._model_contradiction_hint", return_value=None):
            api.store_memory("knowledge", "FortiGate admin access stays restricted", source="test")
        first = api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        with mock.patch(
            "ocmemog.runtime.memory.api._model_contradiction_hint",
            return_value={"contradiction": True, "confidence": 0.99, "rationale": "same subject, different port"},
        ):
            api.store_memory("knowledge", "Gateway should run on port 17890", source="test")

        result = app.memory_governance_review(
            app.GovernanceReviewRequest(categories=["knowledge"], limit=20, context_depth=1)
        )

        self.assertTrue(result["ok"])
        items = result["items"]
        kinds = {item["kind"] for item in items}
        self.assertIn("duplicate_candidate", kinds)
        self.assertIn("contradiction_candidate", kinds)
        self.assertIn("supersession_recommendation", kinds)

        duplicate_item = next(item for item in items if item["kind"] == "duplicate_candidate")
        self.assertEqual(duplicate_item["relationship"], "duplicate_of")
        self.assertEqual(duplicate_item["target"]["reference"], f"knowledge:{canonical}")
        self.assertEqual(duplicate_item["actions"][0]["decision"], "approve")
        self.assertIn("explanation", duplicate_item)
        self.assertIn("short", duplicate_item["explanation"])
        self.assertEqual(duplicate_item["explanation"]["source_status"], "active")
        self.assertIn("priority_label", duplicate_item)

        supersession_item = next(item for item in items if item["kind"] == "supersession_recommendation")
        self.assertEqual(supersession_item["source"]["reference"], "knowledge:4")
        self.assertEqual(supersession_item["target"]["reference"], f"knowledge:{first}")
        self.assertGreater(supersession_item["signal"], 0.0)
        self.assertIn("Possible supersession", supersession_item["explanation"]["short"])

    def test_governance_decision_endpoint_can_promote_duplicate(self) -> None:
        canonical = api.store_memory("knowledge", "FortiGate admin access stays restricted", source="test")
        with mock.patch("ocmemog.runtime.memory.api._model_contradiction_hint", return_value=None):
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

        with mock.patch("ocmemog.runtime.memory.vector_index.search_memory", return_value=[]):
            search = retrieval.retrieve("FortiGate admin access", limit=10, categories=["knowledge"])
        refs = [item["memory_reference"] for item in search["knowledge"]]
        self.assertIn(f"knowledge:{canonical}", refs)
        self.assertNotIn(f"knowledge:{duplicate}", refs)

    def test_governance_review_decision_endpoint_applies_and_clears_pending_item(self) -> None:
        canonical = api.store_memory("knowledge", "FortiGate admin access stays restricted", source="test")
        with mock.patch("ocmemog.runtime.memory.api._model_contradiction_hint", return_value=None):
            duplicate = api.store_memory("knowledge", "FortiGate admin access stays restricted", source="test")

        result = app.memory_governance_review_decision(
            app.GovernanceReviewDecisionRequest(
                reference=f"knowledge:{duplicate}",
                target_reference=f"knowledge:{canonical}",
                kind="duplicate_candidate",
                approved=True,
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["relationship"], "duplicate_of")
        self.assertEqual(result["result"]["source"]["memory_status"], "duplicate")

        review = app.memory_governance_review(app.GovernanceReviewRequest(categories=["knowledge"], limit=20))
        duplicate_reviews = [
            item for item in review["items"]
            if item["kind"] == "duplicate_candidate"
            and item["reference"] == f"knowledge:{duplicate}"
            and item["target_reference"] == f"knowledge:{canonical}"
        ]
        self.assertEqual(duplicate_reviews, [])

    def test_governance_review_decision_endpoint_can_reject_supersession_recommendation(self) -> None:
        first = api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        with mock.patch(
            "ocmemog.runtime.memory.api._model_contradiction_hint",
            return_value={"contradiction": True, "confidence": 0.99, "rationale": "same subject, different port"},
        ):
            second = api.store_memory("knowledge", "Gateway should run on port 17890", source="test")

        result = app.memory_governance_review_decision(
            app.GovernanceReviewDecisionRequest(
                reference=f"knowledge:{second}",
                target_reference=f"knowledge:{first}",
                kind="supersession_recommendation",
                approved=False,
            )
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["relationship"], "supersedes")

        payload = provenance.fetch_reference(f"knowledge:{second}")
        prov = ((payload or {}).get("metadata") or {}).get("provenance") or {}
        self.assertFalse(prov.get("supersession_recommendation"))

        review = app.memory_governance_review(app.GovernanceReviewRequest(categories=["knowledge"], limit=20))
        pending = [
            item for item in review["items"]
            if item["kind"] == "supersession_recommendation"
            and item["reference"] == f"knowledge:{second}"
            and item["target_reference"] == f"knowledge:{first}"
        ]
        self.assertEqual(pending, [])

    def test_governance_review_summary_exposes_diagnostics_and_cache_state(self) -> None:
        canonical = api.store_memory("knowledge", "FortiGate admin access stays restricted", source="test")
        with mock.patch("ocmemog.runtime.memory.api._model_contradiction_hint", return_value=None):
            api.store_memory("knowledge", "FortiGate admin access stays restricted", source="test")

        first = app.memory_governance_review_summary(app.GovernanceReviewRequest(categories=["knowledge"], limit=20))
        self.assertTrue(first["ok"])
        self.assertFalse(first["cached"])
        self.assertIn("reviewDiagnostics", first)
        self.assertFalse(first["reviewDiagnostics"]["cache_hit"])
        self.assertGreaterEqual(first["reviewDiagnostics"]["item_count"], 1)
        self.assertIn("duplicate_candidate", first["reviewDiagnostics"]["kind_counts"])
        self.assertIn("priority_label_counts", first["reviewDiagnostics"])

        second = app.memory_governance_review_summary(app.GovernanceReviewRequest(categories=["knowledge"], limit=20))
        self.assertTrue(second["cached"])
        self.assertTrue(second["reviewDiagnostics"]["cache_hit"])
        self.assertGreaterEqual(second["reviewDiagnostics"]["cache_ttl_seconds"], 0.0)


if __name__ == "__main__":
    unittest.main()
