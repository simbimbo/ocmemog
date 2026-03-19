from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ocmemog.sidecar import app
from brain.runtime.memory import api, provenance, store


class ContradictionCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)
        store._SCHEMA_READY = False

    def test_find_contradiction_candidates_detects_changed_literals(self) -> None:
        first = api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        second = api.store_memory("knowledge", "Gateway should run on port 17890", source="test")
        api.store_memory("knowledge", "Weather is sunny in Boston", source="test")

        with mock.patch("brain.runtime.memory.api._model_contradiction_hint", return_value=None):
            candidates = api.find_contradiction_candidates(f"knowledge:{first}", limit=5, min_signal=0.4, use_model=False)

        refs = [item["reference"] for item in candidates]
        self.assertIn(f"knowledge:{second}", refs)
        updated = provenance.fetch_reference(f"knowledge:{first}") or {}
        prov = (updated.get("metadata") or {}).get("provenance") or {}
        self.assertIn(f"knowledge:{second}", prov.get("contradicts") or [])
        self.assertEqual(prov.get("contradiction_status"), "candidate")

    def test_sidecar_contradiction_candidates_endpoint_includes_model_hint(self) -> None:
        first = api.store_memory("knowledge", "Gateway should run on port 18789", source="test")
        second = api.store_memory("knowledge", "Gateway should run on port 17890", source="test")

        with mock.patch(
            "brain.runtime.memory.api._model_contradiction_hint",
            return_value={"contradiction": True, "confidence": 0.91, "rationale": "same subject, different port"},
        ):
            result = app.memory_contradiction_candidates(
                app.ContradictionCandidatesRequest(reference=f"knowledge:{first}", limit=5, min_signal=0.4, use_model=True)
            )

        refs = [item["reference"] for item in result["candidates"]]
        self.assertIn(f"knowledge:{second}", refs)
        hit = next(item for item in result["candidates"] if item["reference"] == f"knowledge:{second}")
        self.assertTrue(hit["model_hint"]["contradiction"])
        self.assertGreaterEqual(hit["signal"], 0.91)


if __name__ == "__main__":
    unittest.main()
