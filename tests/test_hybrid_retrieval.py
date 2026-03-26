from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ocmemog.runtime.memory import api, reinforcement, retrieval, store


class HybridRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)
        store._SCHEMA_READY = False

    def test_combines_lexical_and_semantic_signals(self) -> None:
        first = api.store_memory("knowledge", "fortigate edge baseline hardening", source="test")
        second = api.store_memory("knowledge", "management plane lockdown and admin isolation", source="test")

        with mock.patch("ocmemog.runtime.memory.vector_index.search_memory", return_value=[
            {"source_type": "knowledge", "source_id": str(second), "score": 0.88},
            {"source_type": "knowledge", "source_id": str(first), "score": 0.12},
        ]):
            results = retrieval.retrieve("fortigate hardening", limit=5, categories=["knowledge"])

        knowledge = results["knowledge"]
        refs = [item["memory_reference"] for item in knowledge]
        self.assertIn(f"knowledge:{first}", refs)
        self.assertIn(f"knowledge:{second}", refs)

        semantic_item = next(item for item in knowledge if item["memory_reference"] == f"knowledge:{second}")
        self.assertGreater(semantic_item["retrieval_signals"]["semantic"], 0.0)

    def test_exposes_selection_reason_and_signal_breakdown(self) -> None:
        row_id = api.store_memory("knowledge", "checkpoint expansion should stay enabled", source="test")

        with mock.patch("ocmemog.runtime.memory.vector_index.search_memory", return_value=[
            {"source_type": "knowledge", "source_id": str(row_id), "score": 0.91},
        ]):
            results = retrieval.retrieve("checkpoint expansion", limit=5, categories=["knowledge"])

        item = next(entry for entry in results["knowledge"] if entry["memory_reference"] == f"knowledge:{row_id}")
        self.assertIn(item["selected_because"], {"keyword", "semantic", "reinforcement", "promotion", "recency"})
        self.assertIn("semantic", item["retrieval_signals"])
        self.assertIn("keyword", item["retrieval_signals"])

    def test_lexical_score_handles_partial_phrases_and_prefix_matches(self) -> None:
        row_id = api.store_memory("knowledge", "predictive hydration for checkpoint expansions", source="test")

        with mock.patch("ocmemog.runtime.memory.vector_index.search_memory", return_value=[]):
            results = retrieval.retrieve("predict hydr checkpoint expand", limit=5, categories=["knowledge"])

        item = next(entry for entry in results["knowledge"] if entry["memory_reference"] == f"knowledge:{row_id}")
        self.assertGreater(item["retrieval_signals"]["keyword"], 0.0)
        self.assertEqual(item["selected_because"], "keyword")

    def test_exposes_compact_governance_summary_in_retrieval_results(self) -> None:
        first = api.store_memory("knowledge", "gateway should run on port 18789", source="test")
        with mock.patch(
            "ocmemog.runtime.memory.api._model_contradiction_hint",
            return_value={"contradiction": True, "confidence": 0.99, "rationale": "same subject, different port"},
        ):
            second = api.store_memory("knowledge", "gateway should run on port 17890", source="test")

        with mock.patch("ocmemog.runtime.memory.vector_index.search_memory", return_value=[]):
            results = retrieval.retrieve("gateway port", limit=5, categories=["knowledge"])

        item = next(entry for entry in results["knowledge"] if entry["memory_reference"] == f"knowledge:{second}")
        self.assertIn("governance_summary", item)
        self.assertEqual(item["governance_summary"]["memory_status"], item["memory_status"])
        self.assertEqual(item["governance_summary"]["contradiction_count"], len(item["governance"].get("contradicts") or []))
        self.assertIn("needs_review", item["governance_summary"])

    def test_reinforcement_counts_influence_retrieval_signals(self) -> None:
        row_id = api.store_memory("knowledge", "FortiGate hardening baseline", source="test")
        for idx in range(3):
            reinforcement.log_experience(
                task_id=f"task-{idx}",
                outcome="success",
                confidence=1.0,
                reward_score=1.0,
                memory_reference=f"knowledge:{row_id}",
                experience_type="retrieval_feedback",
                source_module="test",
            )

        with mock.patch("ocmemog.runtime.memory.vector_index.search_memory", return_value=[]):
            results = retrieval.retrieve("FortiGate baseline", limit=5, categories=["knowledge"])

        item = next(entry for entry in results["knowledge"] if entry["memory_reference"] == f"knowledge:{row_id}")
        self.assertGreater(item["retrieval_signals"]["reinforcement"], 0.0)
        self.assertEqual(item["retrieval_signals"]["reinforcement_count"], 3.0)
        self.assertGreater(item["retrieval_signals"]["reinforcement_weighted_count"], 0.0)

    def test_recent_reinforcement_outweighs_stale_reinforcement(self) -> None:
        recent_id = api.store_memory("knowledge", "recent FortiGate baseline", source="test")
        stale_id = api.store_memory("knowledge", "stale FortiGate baseline", source="test")
        reinforcement.log_experience(
            task_id="recent-task",
            outcome="success",
            confidence=1.0,
            reward_score=1.0,
            memory_reference=f"knowledge:{recent_id}",
            experience_type="retrieval_feedback",
            source_module="test",
        )
        reinforcement.log_experience(
            task_id="stale-task",
            outcome="success",
            confidence=1.0,
            reward_score=1.0,
            memory_reference=f"knowledge:{stale_id}",
            experience_type="retrieval_feedback",
            source_module="test",
        )

        conn = store.connect()
        conn.execute(
            "UPDATE experiences SET timestamp='2025-01-01 00:00:00' WHERE task_id='stale-task'"
        )
        conn.commit()
        conn.close()

        with mock.patch("ocmemog.runtime.memory.vector_index.search_memory", return_value=[]):
            results = retrieval.retrieve("FortiGate baseline", limit=10, categories=["knowledge"])

        recent_item = next(entry for entry in results["knowledge"] if entry["memory_reference"] == f"knowledge:{recent_id}")
        stale_item = next(entry for entry in results["knowledge"] if entry["memory_reference"] == f"knowledge:{stale_id}")
        self.assertGreater(recent_item["retrieval_signals"]["reinforcement"], stale_item["retrieval_signals"]["reinforcement"])
        self.assertGreater(recent_item["retrieval_signals"]["reinforcement_weighted_count"], stale_item["retrieval_signals"]["reinforcement_weighted_count"])


if __name__ == "__main__":
    unittest.main()
