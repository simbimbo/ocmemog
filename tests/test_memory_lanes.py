from __future__ import annotations

import json
import os
import tempfile
import unittest

from ocmemog.runtime.memory import store
from ocmemog.sidecar import app


class MemoryLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        os.environ["OCMEMOG_TRANSCRIPT_ROOTS"] = self.tempdir.name
        os.environ["OCMEMOG_MEMORY_LANES_JSON"] = json.dumps({
            "tbc": {
                "keywords": ["tbc", "librenms", "dal", "atl", "slp", "pga", "smartnet", "warranty"],
                "metadata_filters": {"domain": "tbc"},
            }
        })
        store._SCHEMA_READY = False
        app.QUEUE_STATS.update({
            "last_run": None,
            "processed": 0,
            "errors": 0,
            "last_error": None,
            "last_batch": 0,
        })

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)
        os.environ.pop("OCMEMOG_TRANSCRIPT_ROOTS", None)
        os.environ.pop("OCMEMOG_MEMORY_LANES_JSON", None)
        store._SCHEMA_READY = False

    def test_memory_ingest_accepts_structured_metadata_and_search_filters_by_it(self) -> None:
        tbc = app.memory_ingest(
            app.IngestRequest(
                content="DAL custom map should remain location-first and drill into devices.",
                kind="memory",
                memory_type="knowledge",
                source="unit-test",
                metadata={"domain": "tbc", "site": "dal", "sensitivity": "restricted"},
            )
        )
        self.assertTrue(tbc["ok"])

        general = app.memory_ingest(
            app.IngestRequest(
                content="Remember to buy coffee filters tomorrow morning.",
                kind="memory",
                memory_type="knowledge",
                source="unit-test",
                metadata={"domain": "general"},
            )
        )
        self.assertTrue(general["ok"])

        results = app.memory_search(
            app.SearchRequest(
                query="map devices",
                limit=5,
                categories=["knowledge"],
                metadata_filters={"domain": "tbc"},
            )
        )
        self.assertTrue(results["ok"])
        refs = [item["memory_reference"] for item in results["results"]]
        self.assertIn(tbc["reference"], refs)
        self.assertNotIn(general["reference"], refs)

    def test_tbc_lane_is_inferred_from_query_and_biases_tbc_memories_first(self) -> None:
        tbc = app.memory_ingest(
            app.IngestRequest(
                content="DAL provider circuit map notes and LibreNMS drilldown state.",
                kind="memory",
                memory_type="knowledge",
                source="unit-test",
                metadata={"domain": "tbc", "site": "dal"},
            )
        )
        self.assertTrue(tbc["ok"])

        general = app.memory_ingest(
            app.IngestRequest(
                content="DAL provider circuit map notes and LibreNMS drilldown state.",
                kind="memory",
                memory_type="knowledge",
                source="unit-test",
                metadata={"domain": "general"},
            )
        )
        self.assertTrue(general["ok"])

        results = app.memory_search(
            app.SearchRequest(
                query="TBC DAL LibreNMS provider circuit map",
                limit=5,
                categories=["knowledge"],
            )
        )
        self.assertTrue(results["ok"])
        self.assertGreaterEqual(len(results["results"]), 2)
        self.assertEqual(results["results"][0]["memory_reference"], tbc["reference"])


if __name__ == "__main__":
    unittest.main()
