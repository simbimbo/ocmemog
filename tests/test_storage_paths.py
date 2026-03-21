from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ocmemog.runtime import storage_paths


class StoragePathsTests(unittest.TestCase):
    def test_root_dir_prefers_ocmemog_state_dir(self) -> None:
        with tempfile.TemporaryDirectory() as oc_state, tempfile.TemporaryDirectory() as brain_state:
            with mock.patch.dict(os.environ, {"OCMEMOG_STATE_DIR": oc_state, "BRAIN_STATE_DIR": brain_state}, clear=False):
                self.assertEqual(storage_paths.root_dir(), Path(oc_state).resolve())

    def test_root_dir_uses_brain_fallback_for_blank_ocmemog_state_dir(self) -> None:
        with tempfile.TemporaryDirectory() as brain_state:
            with mock.patch.dict(
                os.environ,
                {"OCMEMOG_STATE_DIR": "   ", "BRAIN_STATE_DIR": brain_state},
                clear=False,
            ):
                self.assertEqual(storage_paths.root_dir(), Path(brain_state).resolve())

    def test_memory_db_path_trimmed_override(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            override = Path(root) / "custom.sqlite3"
            with mock.patch.dict(os.environ, {"OCMEMOG_DB_PATH": f"  {override}  "}, clear=False):
                self.assertEqual(storage_paths.memory_db_path(), override.resolve())


if __name__ == "__main__":
    unittest.main()
