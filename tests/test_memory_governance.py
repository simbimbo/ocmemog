from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ocmemog.runtime.memory import api, retrieval, store


class MemoryGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)
        store._SCHEMA_READY = False

    def test_superseded_memory_is_suppressed_from_retrieval(self) -> None:
        old_id = api.store_memory("knowledge", "Steven's old phone number is 555-0000", source="test")
        new_id = api.store_memory("knowledge", "Steven's phone number is 508-361-2323", source="test")
        api.mark_memory_relationship(f"knowledge:{new_id}", relationship="supersedes", target_reference=f"knowledge:{old_id}")

        with mock.patch("ocmemog.runtime.memory.vector_index.search_memory", return_value=[]):
            results = retrieval.retrieve("Steven phone number", limit=10, categories=["knowledge"])

        refs = [item["memory_reference"] for item in results["knowledge"]]
        self.assertIn(f"knowledge:{new_id}", refs)
        self.assertNotIn(f"knowledge:{old_id}", refs)

    def test_duplicate_memory_is_suppressed_from_retrieval(self) -> None:
        canonical_id = api.store_memory("knowledge", "FortiGate admin access should stay restricted", source="test")
        dup_id = api.store_memory("knowledge", "FortiGate admin access should stay restricted", source="test")
        api.mark_memory_relationship(f"knowledge:{dup_id}", relationship="duplicate_of", target_reference=f"knowledge:{canonical_id}")

        with mock.patch("ocmemog.runtime.memory.vector_index.search_memory", return_value=[]):
            results = retrieval.retrieve("FortiGate admin access", limit=10, categories=["knowledge"])

        refs = [item["memory_reference"] for item in results["knowledge"]]
        self.assertIn(f"knowledge:{canonical_id}", refs)
        self.assertNotIn(f"knowledge:{dup_id}", refs)

    def test_contested_memory_remains_visible_with_penalty_and_governance(self) -> None:
        first_id = api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        second_id = api.store_memory("knowledge", "Gateway should run on port 17890", source="test")
        api.mark_memory_relationship(f"knowledge:{second_id}", relationship="contradicts", target_reference=f"knowledge:{first_id}")

        with mock.patch("ocmemog.runtime.memory.vector_index.search_memory", return_value=[]):
            results = retrieval.retrieve("Gateway port", limit=10, categories=["knowledge"])

        contested = next(item for item in results["knowledge"] if item["memory_reference"] == f"knowledge:{second_id}")
        self.assertEqual(contested["memory_status"], "contested")
        self.assertEqual(contested["governance"]["contradiction_status"], "contested")
        self.assertIn("contradiction_penalty", contested["retrieval_signals"])

    def test_retrieval_diagnostics_track_hidden_governance_suppression(self) -> None:
        old_id = api.store_memory("knowledge", "Steven's old phone number is 555-0000", source="test")
        new_id = api.store_memory("knowledge", "Steven's phone number is 508-361-2323", source="test")
        dup_id = api.store_memory("knowledge", "Steven's phone number is 508-361-2323", source="test")
        api.mark_memory_relationship(f"knowledge:{new_id}", relationship="supersedes", target_reference=f"knowledge:{old_id}")
        api.mark_memory_relationship(f"knowledge:{dup_id}", relationship="duplicate_of", target_reference=f"knowledge:{new_id}")

        with mock.patch("ocmemog.runtime.memory.vector_index.search_memory", return_value=[]):
            retrieval.retrieve("Steven phone number", limit=10, categories=["knowledge"])

        diagnostics = retrieval.get_last_retrieval_diagnostics()
        self.assertEqual(diagnostics["suppressed_by_governance"]["superseded"], 1)
        self.assertEqual(diagnostics["suppressed_by_governance"]["duplicate"], 1)


if __name__ == "__main__":
    unittest.main()
