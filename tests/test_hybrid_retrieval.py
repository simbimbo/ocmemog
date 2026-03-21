from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ocmemog.runtime.memory import api, retrieval, store


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


if __name__ == "__main__":
    unittest.main()
