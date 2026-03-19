from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ocmemog.sidecar import app
from brain.runtime.memory import api, provenance, retrieval, store


class GovernanceRollbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)
        store._SCHEMA_READY = False

    def test_rollback_duplicate_restores_visibility(self) -> None:
        canonical = api.store_memory("knowledge", "FortiGate admin access stays restricted", source="test")
        duplicate = api.store_memory("knowledge", "FortiGate admin access stays restricted", source="test")
        api.mark_memory_relationship(f"knowledge:{duplicate}", relationship="duplicate_of", target_reference=f"knowledge:{canonical}")

        rollback = app.memory_governance_rollback(
            app.GovernanceRollbackRequest(
                reference=f"knowledge:{duplicate}",
                relationship="duplicate_of",
                target_reference=f"knowledge:{canonical}",
            )
        )
        self.assertTrue(rollback["ok"])

        with mock.patch("brain.runtime.memory.vector_index.search_memory", return_value=[]):
            results = retrieval.retrieve("FortiGate admin access", limit=10, categories=["knowledge"])
        refs = [item["memory_reference"] for item in results["knowledge"]]
        self.assertIn(f"knowledge:{canonical}", refs)
        self.assertIn(f"knowledge:{duplicate}", refs)

    def test_rollback_supersedes_restores_old_memory(self) -> None:
        old_id = api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        new_id = api.store_memory("knowledge", "Gateway should run on port 17890", source="test")
        api.mark_memory_relationship(f"knowledge:{new_id}", relationship="supersedes", target_reference=f"knowledge:{old_id}")

        rollback = app.memory_governance_rollback(
            app.GovernanceRollbackRequest(
                reference=f"knowledge:{new_id}",
                relationship="supersedes",
                target_reference=f"knowledge:{old_id}",
            )
        )
        self.assertTrue(rollback["ok"])

        old_payload = provenance.fetch_reference(f"knowledge:{old_id}") or {}
        old_prov = (old_payload.get("metadata") or {}).get("provenance") or {}
        self.assertEqual(old_prov.get("memory_status"), "active")


if __name__ == "__main__":
    unittest.main()
