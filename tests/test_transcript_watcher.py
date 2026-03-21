import os
import tempfile
import unittest
from unittest import mock

from ocmemog.sidecar import transcript_watcher


class TranscriptWatcherTests(unittest.TestCase):
    def test_append_partial_line_caps_retained_tail(self) -> None:
        state = {"partial_lines": {}}
        chunk = b"x" * (transcript_watcher.MAX_PARTIAL_LINE_BYTES + 100)

        emitted = transcript_watcher._append_partial_line(state, "sample.log", chunk)

        self.assertEqual(emitted, b"")
        retained = state["partial_lines"]["sample.log"]
        self.assertEqual(len(retained.encode("utf-8")), transcript_watcher.MAX_PARTIAL_LINE_BYTES)
        self.assertTrue(retained.endswith("x" * 32))

    def test_load_state_logs_and_recovers_from_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(os.environ, {"OCMEMOG_STATE_DIR": tmpdir}, clear=False):
                state_path = transcript_watcher._state_path()
                state_path.write_text("{not-json", encoding="utf-8")
                with mock.patch("sys.stderr") as stderr:
                    state = transcript_watcher._load_state()
        self.assertEqual(state, {})
        written = "".join(call.args[0] for call in stderr.write.call_args_list if call.args)
        self.assertIn("state_load_failed", written)

    def test_save_state_writes_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(os.environ, {"OCMEMOG_STATE_DIR": tmpdir}, clear=False):
                state_path = transcript_watcher._state_path()
                transcript_watcher._save_state({"offsets": {"a.log": 12}})
                self.assertTrue(state_path.exists())
                self.assertFalse(state_path.with_suffix(state_path.suffix + ".tmp").exists())
                loaded = transcript_watcher._load_state()
        self.assertEqual(loaded, {"offsets": {"a.log": 12}})

    def test_load_state_rejects_non_dict_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(os.environ, {"OCMEMOG_STATE_DIR": tmpdir}, clear=False):
                state_path = transcript_watcher._state_path()
                state_path.write_text("[]", encoding="utf-8")
                with mock.patch("sys.stderr") as stderr:
                    state = transcript_watcher._load_state()
        self.assertEqual(state, {})
        written = "".join(call.args[0] for call in stderr.write.call_args_list if call.args)
        self.assertIn("state_load_invalid_type", written)


if __name__ == "__main__":
    unittest.main()
