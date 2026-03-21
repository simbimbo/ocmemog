from __future__ import annotations

import os
import tempfile
import unittest

from ocmemog.sidecar import app
from ocmemog.runtime.memory import api, provenance, store


class DuplicateCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)
        store._SCHEMA_READY = False

    def test_find_duplicate_candidates_returns_similar_memories_and_updates_metadata(self) -> None:
        first = api.store_memory("knowledge", "FortiGate admin access must stay tightly restricted", source="test")
        second = api.store_memory("knowledge", "FortiGate admin access should remain tightly restricted", source="test")
        api.store_memory("knowledge", "Completely unrelated memory about weather", source="test")

        candidates = api.find_duplicate_candidates(f"knowledge:{first}", limit=5, min_similarity=0.5)
        refs = [item["reference"] for item in candidates]
        self.assertIn(f"knowledge:{second}", refs)

        updated = provenance.fetch_reference(f"knowledge:{first}") or {}
        metadata = updated.get("metadata") or {}
        prov = metadata.get("provenance") or {}
        self.assertIn(f"knowledge:{second}", prov.get("duplicate_candidates") or [])

    def test_sidecar_duplicate_candidates_endpoint_returns_candidates(self) -> None:
        first = api.store_memory("knowledge", "Gateway port should stay on 18789", source="test")
        second = api.store_memory("knowledge", "The gateway port should remain 18789", source="test")

        result = app.memory_duplicate_candidates(
            app.DuplicateCandidatesRequest(reference=f"knowledge:{first}", limit=5, min_similarity=0.3)
        )
        refs = [item["reference"] for item in result["candidates"]]
        self.assertIn(f"knowledge:{second}", refs)


if __name__ == "__main__":
    unittest.main()
