from __future__ import annotations

import json
import os
import tempfile
import unittest

from ocmemog.runtime.memory import health, integrity, store


class MemoryIntegrityHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.env = os.environ
        self.env["OCMEMOG_STATE_DIR"] = self.tempdir.name

    def tearDown(self) -> None:
        self.env.pop("OCMEMOG_STATE_DIR", None)
        self.tempdir.cleanup()

    def test_run_integrity_check_counts_invalid_source_id_orphans(self) -> None:
        conn = store.connect()
        try:
            conn.execute(
                "INSERT INTO knowledge (source, confidence, metadata_json, content, schema_version) VALUES (?, ?, ?, ?, ?)",
                ("health-test", 1.0, json.dumps({}), "knowledge row with vector", store.SCHEMA_VERSION),
            )
            conn.execute(
                "INSERT INTO knowledge (source, confidence, metadata_json, content, schema_version) VALUES (?, ?, ?, ?, ?)",
                ("health-test", 1.0, json.dumps({}), "knowledge row without vector", store.SCHEMA_VERSION),
            )
            conn.execute(
                "INSERT INTO vector_embeddings (id, source_type, source_id, embedding) VALUES (?, ?, ?, ?)",
                ("knowledge:1", "knowledge", "1", "[]"),
            )
            conn.execute(
                "INSERT INTO vector_embeddings (id, source_type, source_id, embedding) VALUES (?, ?, ?, ?)",
                ("knowledge:orphan-empty", "knowledge", "", "[]"),
            )
            conn.execute(
                "INSERT INTO vector_embeddings (id, source_type, source_id, embedding) VALUES (?, ?, ?, ?)",
                ("knowledge:orphan-missing", "knowledge", "999", "[]"),
            )
            conn.commit()
        finally:
            conn.close()

        result = integrity.run_integrity_check()
        missing_entry = next(
            item for item in result["issues"] if isinstance(item, str) and item.startswith("vector_missing:")
        )
        orphan_entry = next(item for item in result["issues"] if isinstance(item, str) and item.startswith("vector_orphan:"))

        self.assertEqual(missing_entry, "vector_missing:1")
        self.assertEqual(orphan_entry, "vector_orphan:2")

    def test_get_memory_health_coverage_uses_source_coverage(self) -> None:
        conn = store.connect()
        try:
            conn.execute(
                "INSERT INTO knowledge (source, confidence, metadata_json, content, schema_version) VALUES (?, ?, ?, ?, ?)",
                ("health-test", 1.0, json.dumps({}), "knowledge row with vector", store.SCHEMA_VERSION),
            )
            conn.execute(
                "INSERT INTO knowledge (source, confidence, metadata_json, content, schema_version) VALUES (?, ?, ?, ?, ?)",
                ("health-test", 1.0, json.dumps({}), "knowledge row without vector", store.SCHEMA_VERSION),
            )
            conn.execute(
                "INSERT INTO vector_embeddings (id, source_type, source_id, embedding) VALUES (?, ?, ?, ?)",
                ("knowledge:1", "knowledge", "1", "[]"),
            )
            conn.execute(
                "INSERT INTO vector_embeddings (id, source_type, source_id, embedding) VALUES (?, ?, ?, ?)",
                ("knowledge:orphan-empty", "knowledge", "", "[]"),
            )
            conn.commit()
        finally:
            conn.close()

        payload = health.get_memory_health()
        self.assertEqual(payload["vector_index_count"], 2)
        self.assertEqual(payload["vector_index_coverage"], 0.5)
