from __future__ import annotations

import os
import tempfile
import unittest

from ocmemog.runtime.memory import store


class StoreSchemaTests(unittest.TestCase):
    def test_connect_reinitializes_schema_when_state_dir_changes(self) -> None:
        first_state = tempfile.TemporaryDirectory()
        second_state = tempfile.TemporaryDirectory()
        try:
            os.environ["OCMEMOG_STATE_DIR"] = first_state.name
            store._SCHEMA_READY = False
            first_conn = store.connect()
            first_conn.execute(
                "INSERT INTO knowledge (source, content, schema_version) VALUES (?, ?, ?)",
                ("test", "Gateway should run on port 18789", "v1"),
            )
            first_conn.commit()
            first_conn.close()

            os.environ["OCMEMOG_STATE_DIR"] = second_state.name
            second_conn = store.connect()
            try:
                row_count = second_conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
            finally:
                second_conn.close()

            self.assertEqual(row_count, 0)
        finally:
            first_state.cleanup()
            second_state.cleanup()
            os.environ.pop("OCMEMOG_STATE_DIR", None)
            store._SCHEMA_READY = False
            store._SCHEMA_DB_PATH = None
