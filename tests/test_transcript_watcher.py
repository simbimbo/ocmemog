import unittest

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


if __name__ == "__main__":
    unittest.main()
