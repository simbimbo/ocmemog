from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from ocmemog.runtime.memory import candidate, distill, store
from ocmemog.sidecar import app


class CandidateNearDuplicateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        store._SCHEMA_READY = False

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)
        store._SCHEMA_READY = False

    def test_near_identical_candidates_with_shared_transcript_anchor_collapse(self) -> None:
        shared_metadata = {
            "session_id": "sess-shared",
            "thread_id": "thread-shared",
            "message_id": "msg-shared",
            "transcript_path": "/tmp/shared.log",
            "transcript_offset": 41,
            "transcript_end_offset": 43,
        }
        first = candidate.create_candidate(
            source_event_id=101,
            distilled_summary="The user prefers Japanese breakfast and quiet boutique hotels.",
            verification_points=["Confirm preference before booking"],
            confidence_score=0.91,
            metadata=shared_metadata,
        )
        second = candidate.create_candidate(
            source_event_id=202,
            distilled_summary="User prefers Japanese breakfast plus quiet boutique hotels.",
            verification_points=["Confirm preference before booking"],
            confidence_score=0.89,
            metadata=shared_metadata,
        )

        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["candidate_id"], second["candidate_id"])

        conn = store.connect()
        try:
            count = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)

    def test_same_summary_without_shared_anchor_stays_distinct(self) -> None:
        first = candidate.create_candidate(
            source_event_id=301,
            distilled_summary="The user prefers green tea in the afternoon.",
            verification_points=["Confirm preference before offering tea"],
            confidence_score=0.9,
            metadata={
                "session_id": "sess-a",
                "thread_id": "thread-a",
                "message_id": "msg-a",
                "transcript_path": "/tmp/a.log",
                "transcript_offset": 11,
                "transcript_end_offset": 11,
            },
        )
        second = candidate.create_candidate(
            source_event_id=302,
            distilled_summary="The user prefers green tea in the afternoon.",
            verification_points=["Confirm preference before offering tea"],
            confidence_score=0.9,
            metadata={
                "session_id": "sess-b",
                "thread_id": "thread-b",
                "message_id": "msg-b",
                "transcript_path": "/tmp/b.log",
                "transcript_offset": 77,
                "transcript_end_offset": 77,
            },
        )

        self.assertFalse(first["duplicate"])
        self.assertFalse(second["duplicate"])
        self.assertNotEqual(first["candidate_id"], second["candidate_id"])

        conn = store.connect()
        try:
            rows = conn.execute("SELECT candidate_id, metadata_json FROM candidates ORDER BY created_at ASC").fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [json.loads(row["metadata_json"])["provenance"]["conversation"]["message_id"] for row in rows],
            ["msg-a", "msg-b"],
        )

    def test_distill_replay_with_shared_anchor_reuses_existing_candidate(self) -> None:
        base_request = dict(
            kind="experience",
            source="test",
            session_id="sess-replay",
            thread_id="thread-replay",
            message_id="msg-replay",
            transcript_path="/tmp/replay.log",
            transcript_offset=8,
            transcript_end_offset=9,
        )
        first_ingest = app._ingest_request(
            app.IngestRequest(
                content="User says they prefer aisle seats on long flights.",
                **base_request,
            )
        )
        self.assertTrue(first_ingest["ok"])

        with mock.patch("ocmemog.runtime.memory.distill._local_distill_summary", return_value="The user prefers aisle seats on long flights."), mock.patch(
            "ocmemog.runtime.memory.distill._needs_frontier_refine",
            return_value=False,
        ):
            first_results = distill.distill_experiences(limit=1)
        self.assertEqual(len(first_results), 1)
        self.assertFalse(first_results[0]["duplicate"])

        second_ingest = app._ingest_request(
            app.IngestRequest(
                content="User says they prefer aisle seats on long flights.",
                **base_request,
            )
        )
        self.assertTrue(second_ingest["ok"])

        with mock.patch(
            "ocmemog.runtime.memory.distill._local_distill_summary",
            return_value="User prefers aisle seating for long flights.",
        ), mock.patch("ocmemog.runtime.memory.distill._needs_frontier_refine", return_value=False):
            second_results = distill.distill_experiences(limit=1)

        self.assertEqual(len(second_results), 1)
        self.assertTrue(second_results[0]["duplicate"])
        self.assertEqual(first_results[0]["candidate_id"], second_results[0]["candidate_id"])

        conn = store.connect()
        try:
            count = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
