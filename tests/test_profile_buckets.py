from __future__ import annotations

import os
import tempfile
import unittest

from ocmemog.runtime.memory import api, promote, retrieval, store
from ocmemog.sidecar import app


class ProfileBucketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)
        store._SCHEMA_READY = False

    def test_store_and_retrieve_preferences_bucket(self) -> None:
        memory_id = api.store_memory("preferences", "I prefer quiet boutique hotels when I travel.", source="test")

        fetched = app.memory_get(app.GetRequest(reference=f"preferences:{memory_id}"))
        results = retrieval.retrieve("quiet boutique hotels", limit=5, categories=["preferences"])

        self.assertTrue(fetched["ok"])
        self.assertEqual(fetched["memory"]["content"], "I prefer quiet boutique hotels when I travel.")
        refs = [item["memory_reference"] for item in results["preferences"]]
        self.assertIn(f"preferences:{memory_id}", refs)

    def test_store_and_retrieve_identity_bucket(self) -> None:
        memory_id = api.store_memory("identity", "My name is Sam and I live in Boston.", source="test")

        fetched = app.memory_get(app.GetRequest(reference=f"identity:{memory_id}"))
        results = retrieval.retrieve("Sam Boston", limit=5, categories=["identity"])

        self.assertTrue(fetched["ok"])
        self.assertEqual(fetched["memory"]["content"], "My name is Sam and I live in Boston.")
        refs = [item["memory_reference"] for item in results["identity"]]
        self.assertIn(f"identity:{memory_id}", refs)

    def test_promote_candidate_routes_preference_summary_to_preferences(self) -> None:
        result = promote.promote_candidate(
            {
                "candidate_id": "cand-pref",
                "source_event_id": "event-pref",
                "distilled_summary": "I prefer quiet boutique hotels when I travel.",
                "confidence_score": 0.95,
                "metadata": {"source_labels": ["distill"]},
            }
        )

        self.assertEqual(result["decision"], "promote")
        self.assertEqual(result["destination"], "preferences")
        self.assertIn("quality_summary", result)
        self.assertIn(result["quality_summary"]["quality"], {"high", "medium"})
        self.assertEqual(result["quality_summary"]["keep_recommendation"], "keep")
        self.assertIn("verification_summary", result)
        self.assertEqual(result["verification_summary"]["status"], "verified")
        self.assertIn("explanation", result)
        self.assertEqual(result["explanation"]["destination"], "preferences")

        conn = store.connect()
        try:
            row = conn.execute("SELECT id FROM preferences ORDER BY id DESC LIMIT 1").fetchone()
            vector = conn.execute(
                "SELECT source_type FROM vector_embeddings WHERE id = ?",
                (f"preferences:{int(row['id'])}",),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(vector["source_type"], "preferences")

    def test_promote_candidate_routes_identity_summary_to_identity(self) -> None:
        result = promote.promote_candidate(
            {
                "candidate_id": "cand-ident",
                "source_event_id": "event-ident",
                "distilled_summary": "My pronouns are she/her and I live in Boston.",
                "confidence_score": 0.95,
                "metadata": {"source_labels": ["distill"]},
            }
        )

        self.assertEqual(result["decision"], "promote")
        self.assertEqual(result["destination"], "identity")
        self.assertIn("verification_summary", result)
        self.assertEqual(result["verification_summary"]["status"], "verified")
        self.assertIn("explanation", result)
        self.assertEqual(result["explanation"]["destination"], "identity")

        conn = store.connect()
        try:
            row = conn.execute("SELECT id FROM identity ORDER BY id DESC LIMIT 1").fetchone()
            vector = conn.execute(
                "SELECT source_type FROM vector_embeddings WHERE id = ?",
                (f"identity:{int(row['id'])}",),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(vector["source_type"], "identity")

    def test_reject_candidate_uses_richer_reason_for_generic_destination(self) -> None:
        result = promote.promote_candidate(
            {
                "candidate_id": "cand-reject-generic",
                "source_event_id": "event-reject-generic",
                "distilled_summary": "There was a conversation about something important.",
                "confidence_score": 0.25,
                "metadata": {"source_labels": ["distill"]},
            }
        )

        self.assertEqual(result["decision"], "reject")
        self.assertEqual(result["destination"], "knowledge")
        self.assertIn("quality_summary", result)
        self.assertEqual(result["quality_summary"]["quality"], "low")
        self.assertEqual(result["quality_summary"]["keep_recommendation"], "drop")
        self.assertEqual(result["quality_summary"]["noise_risk"], "high")
        self.assertEqual(result["verification_summary"]["reason"], "rejected_as_generic_cruft")
        self.assertEqual(result["explanation"]["reason"], "rejected_as_generic_cruft")

    def test_reject_candidate_flags_redundant_generic_cruft(self) -> None:
        promote.promote_candidate(
            {
                "candidate_id": "cand-existing-generic",
                "source_event_id": "event-existing-generic",
                "distilled_summary": "There was a conversation about something important.",
                "confidence_score": 0.95,
                "metadata": {"source_labels": ["distill"]},
            }
        )
        result = promote.promote_candidate(
            {
                "candidate_id": "cand-redundant-generic",
                "source_event_id": "event-redundant-generic",
                "distilled_summary": "There was a conversation about something important.",
                "confidence_score": 0.25,
                "metadata": {"source_labels": ["distill"]},
            }
        )

        self.assertEqual(result["decision"], "reject")
        self.assertEqual(result["destination"], "knowledge")
        self.assertTrue(result["quality_summary"]["redundant_generic"])
        self.assertEqual(result["verification_summary"]["reason"], "rejected_as_redundant_generic_cruft")
        self.assertEqual(result["explanation"]["reason"], "rejected_as_redundant_generic_cruft")

    def test_reject_candidate_flags_ambiguous_specific_memory(self) -> None:
        result = promote.promote_candidate(
            {
                "candidate_id": "cand-ambiguous-pref",
                "source_event_id": "event-ambiguous-pref",
                "distilled_summary": "I prefer quiet mornings.",
                "confidence_score": 0.45,
                "metadata": {"source_labels": ["distill"]},
            }
        )

        self.assertEqual(result["decision"], "reject")
        self.assertEqual(result["destination"], "preferences")
        self.assertTrue(result["quality_summary"]["ambiguous_specific"])
        self.assertEqual(result["quality_summary"]["keep_recommendation"], "review")
        self.assertEqual(result["verification_summary"]["reason"], "rejected_as_ambiguous_specific_memory")
        self.assertEqual(result["explanation"]["reason"], "rejected_as_ambiguous_specific_memory")


if __name__ == "__main__":
    unittest.main()
