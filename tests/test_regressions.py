from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest import mock

from brain.runtime.memory import api, conversation_state, distill, embedding_engine, pondering_engine, promote, store, vector_index, unresolved_state
from ocmemog.sidecar import app


class OcmemogRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        os.environ["OCMEMOG_TRANSCRIPT_ROOTS"] = self.tempdir.name
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
        store._SCHEMA_READY = False

    def test_memory_ingest_creates_vector_index_entries(self) -> None:
        response = app._ingest_request(
            app.IngestRequest(content="remember provider precedence", kind="memory", memory_type="knowledge")
        )
        self.assertTrue(response["ok"])

        conn = store.connect()
        try:
            vector_rows = conn.execute(
                "SELECT source_type, source_id FROM vector_embeddings WHERE source_type='knowledge'"
            ).fetchall()
            index_rows = conn.execute("SELECT source FROM memory_index WHERE source LIKE 'knowledge:%'").fetchall()
        finally:
            conn.close()

        self.assertEqual(len(vector_rows), 1)
        self.assertEqual(len(index_rows), 1)

    def test_queue_processing_is_durable_on_failure(self) -> None:
        payload_one = {"content": "first", "kind": "memory", "memory_type": "knowledge"}
        payload_two = {"content": "second", "kind": "memory", "memory_type": "knowledge"}
        app._enqueue_payload(payload_one)
        app._enqueue_payload(payload_two)

        original = app._ingest_request
        calls = {"count": 0}

        def flaky(request: app.IngestRequest):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("boom")
            return original(request)

        with mock.patch("ocmemog.sidecar.app._ingest_request", side_effect=flaky):
            stats = app._process_queue(limit=10)
        self.assertEqual(stats["processed"], 0)
        self.assertEqual(app._queue_depth(), 2)

        stats = app._process_queue(limit=10)
        self.assertEqual(stats["processed"], 2)
        self.assertEqual(app._queue_depth(), 0)

    def test_memory_context_uses_transcript_range_anchor(self) -> None:
        transcript = Path(self.tempdir.name) / "sample.log"
        transcript.write_text("\n".join([f"line {idx}" for idx in range(1, 7)]) + "\n", encoding="utf-8")

        response = app._ingest_request(
            app.IngestRequest(
                content="anchored memory",
                kind="memory",
                memory_type="knowledge",
                transcript_path=str(transcript),
                transcript_offset=3,
                transcript_end_offset=4,
            )
        )
        context = app.memory_context(app.ContextRequest(reference=response["reference"], radius=1))
        transcript_payload = context["transcript"]

        self.assertTrue(transcript_payload["ok"])
        self.assertEqual(transcript_payload["anchor_start_line"], 3)
        self.assertEqual(transcript_payload["anchor_end_line"], 4)
        self.assertEqual(transcript_payload["start_line"], 2)
        self.assertEqual(transcript_payload["end_line"], 5)
        self.assertEqual(
            transcript_payload["snippet"],
            "line 2\nline 3\nline 4\nline 5",
        )

    def test_memory_ingest_with_conversation_metadata_creates_turn_and_hydrates(self) -> None:
        transcript = Path(self.tempdir.name) / "conversation.log"
        transcript.write_text("", encoding="utf-8")

        memory_response = app._ingest_request(
            app.IngestRequest(
                content="Need to ship Phase 1A next.",
                kind="memory",
                memory_type="tasks",
                conversation_id="conv-1",
                session_id="sess-1",
                thread_id="thread-1",
                message_id="msg-1",
                role="user",
                transcript_path=str(transcript),
                transcript_offset=10,
                transcript_end_offset=10,
                timestamp="2026-03-15 09:30:00",
            )
        )
        self.assertTrue(memory_response["ok"])
        self.assertTrue(memory_response["turn"]["ok"])

        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(
                conversation_id="conv-1",
                session_id="sess-1",
                thread_id="thread-1",
                turns_limit=5,
                memory_limit=5,
            )
        )

        self.assertTrue(hydrate["ok"])
        self.assertEqual(len(hydrate["recent_turns"]), 1)
        self.assertEqual(hydrate["recent_turns"][0]["role"], "user")
        self.assertEqual(hydrate["recent_turns"][0]["message_id"], "msg-1")
        self.assertEqual(hydrate["summary"]["latest_user_turn"]["content"], "Need to ship Phase 1A next.")
        self.assertEqual(hydrate["summary"]["latest_user_ask"]["content"], "Need to ship Phase 1A next.")
        self.assertEqual(hydrate["turn_counts"]["user"], 1)
        self.assertEqual(hydrate["turn_counts"]["assistant"], 0)
        self.assertEqual(hydrate["linked_memories"][0]["reference"], memory_response["reference"])
        self.assertEqual(hydrate["state"]["latest_user_ask"], "Need to ship Phase 1A next.")
        targets = {item["target_reference"] for item in hydrate["linked_references"]}
        self.assertIn("thread:thread-1", targets)
        self.assertIn("session:sess-1", targets)
        self.assertIn("conversation:conv-1", targets)

    def test_conversation_turn_endpoint_records_recent_turns(self) -> None:
        first = app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="hello",
                session_id="sess-turns",
                thread_id="thread-turns",
                message_id="m1",
                timestamp="2026-03-15 09:00:00",
            )
        )
        second = app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="assistant",
                content="hi there",
                session_id="sess-turns",
                thread_id="thread-turns",
                message_id="m2",
                timestamp="2026-03-15 09:00:01",
            )
        )

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])

        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(session_id="sess-turns", thread_id="thread-turns", turns_limit=5)
        )
        self.assertEqual([turn["message_id"] for turn in hydrate["recent_turns"]], ["m1", "m2"])
        self.assertEqual(hydrate["summary"]["latest_assistant_turn"]["content"], "hi there")

    def test_conversation_checkpoint_and_unresolved_state_enrich_hydration(self) -> None:
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="Can you ship checkpoints next?",
                conversation_id="conv-check",
                session_id="sess-check",
                thread_id="thread-check",
                message_id="u1",
                timestamp="2026-03-15 10:00:00",
            )
        )
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="assistant",
                content="I will add checkpoints and richer hydration output next.",
                conversation_id="conv-check",
                session_id="sess-check",
                thread_id="thread-check",
                message_id="a1",
                timestamp="2026-03-15 10:00:10",
            )
        )
        unresolved_state.add_unresolved_state(
            "paused_task",
            "thread:thread-check",
            "Need to finish unresolved-state integration.",
        )

        checkpoint = app.conversation_checkpoint(
            app.ConversationCheckpointRequest(
                conversation_id="conv-check",
                session_id="sess-check",
                thread_id="thread-check",
                checkpoint_kind="manual",
            )
        )
        self.assertTrue(checkpoint["ok"])
        self.assertEqual(checkpoint["checkpoint"]["checkpoint_kind"], "manual")
        self.assertIn("user asked", checkpoint["checkpoint"]["summary"])

        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(
                conversation_id="conv-check",
                session_id="sess-check",
                thread_id="thread-check",
                turns_limit=5,
            )
        )
        self.assertEqual(hydrate["summary"]["latest_user_ask"]["content"], "Can you ship checkpoints next?")
        self.assertEqual(
            hydrate["summary"]["last_assistant_commitment"]["content"],
            "I will add checkpoints and richer hydration output next.",
        )
        unresolved_summaries = [item["summary"] for item in hydrate["summary"]["unresolved_state"]]
        self.assertIn("Need to finish unresolved-state integration.", unresolved_summaries)
        open_loop_summaries = [item["summary"] for item in hydrate["summary"]["open_loops"]]
        self.assertIn("I will add checkpoints and richer hydration output next.", open_loop_summaries)
        self.assertEqual(hydrate["state"]["last_assistant_commitment"], "I will add checkpoints and richer hydration output next.")
        self.assertEqual(hydrate["state"]["latest_checkpoint_id"], checkpoint["checkpoint"]["id"])

    def test_checkpoint_graph_listing_and_expansion(self) -> None:
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="Let's keep a durable graph.",
                conversation_id="conv-graph",
                session_id="sess-graph",
                thread_id="thread-graph",
                message_id="g-u1",
                timestamp="2026-03-15 11:00:00",
            )
        )
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="assistant",
                content="I will create a checkpoint graph.",
                conversation_id="conv-graph",
                session_id="sess-graph",
                thread_id="thread-graph",
                message_id="g-a1",
                timestamp="2026-03-15 11:00:01",
            )
        )
        first = app.conversation_checkpoint(
            app.ConversationCheckpointRequest(
                conversation_id="conv-graph",
                session_id="sess-graph",
                thread_id="thread-graph",
                checkpoint_kind="manual",
            )
        )
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="Now add checkpoint expansion.",
                conversation_id="conv-graph",
                session_id="sess-graph",
                thread_id="thread-graph",
                message_id="g-u2",
                timestamp="2026-03-15 11:00:02",
            )
        )
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="assistant",
                content="Done, expansion is next.",
                conversation_id="conv-graph",
                session_id="sess-graph",
                thread_id="thread-graph",
                message_id="g-a2",
                timestamp="2026-03-15 11:00:03",
            )
        )
        second = app.conversation_checkpoint(
            app.ConversationCheckpointRequest(
                conversation_id="conv-graph",
                session_id="sess-graph",
                thread_id="thread-graph",
                checkpoint_kind="manual",
                turns_limit=8,
            )
        )

        self.assertEqual(second["checkpoint"]["parent_checkpoint_id"], first["checkpoint"]["id"])
        self.assertEqual(second["checkpoint"]["root_checkpoint_id"], first["checkpoint"]["id"])
        self.assertEqual(second["checkpoint"]["depth"], 1)

        listed = app.conversation_checkpoints(
            app.ConversationCheckpointListRequest(session_id="sess-graph", thread_id="thread-graph", limit=5)
        )
        self.assertEqual([item["id"] for item in listed["checkpoints"]], [second["checkpoint"]["id"], first["checkpoint"]["id"]])

        expanded = app.conversation_checkpoint_expand(
            app.ConversationCheckpointExpandRequest(checkpoint_id=second["checkpoint"]["id"], turns_limit=10)
        )
        self.assertTrue(expanded["ok"])
        self.assertEqual([item["id"] for item in expanded["lineage"]], [first["checkpoint"]["id"], second["checkpoint"]["id"]])
        self.assertEqual([turn["message_id"] for turn in expanded["supporting_turns"]], ["g-u1", "g-a1", "g-u2", "g-a2"])

        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(conversation_id="conv-graph", session_id="sess-graph", thread_id="thread-graph")
        )
        self.assertEqual([item["id"] for item in hydrate["checkpoint_graph"]["lineage"]], [first["checkpoint"]["id"], second["checkpoint"]["id"]])

    def test_short_reply_resolution_and_branch_reply_continuity(self) -> None:
        root_user = app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="Should we ship checkpoints first?",
                conversation_id="conv-branch",
                session_id="sess-branch",
                thread_id="thread-branch",
                message_id="b-u1",
                timestamp="2026-03-15 12:00:00",
            )
        )
        root_assistant = app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="assistant",
                content="Yes — I can ship checkpoints first if you want.",
                conversation_id="conv-branch",
                session_id="sess-branch",
                thread_id="thread-branch",
                message_id="b-a1",
                timestamp="2026-03-15 12:00:01",
            )
        )
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="assistant",
                content="Separately, I can prep branch analytics later.",
                conversation_id="conv-branch",
                session_id="sess-branch",
                thread_id="thread-branch",
                message_id="b-a2",
                timestamp="2026-03-15 12:00:02",
            )
        )
        short_reply = app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="sure",
                conversation_id="conv-branch",
                session_id="sess-branch",
                thread_id="thread-branch",
                message_id="b-u2",
                timestamp="2026-03-15 12:00:03",
                metadata={"reply_to_message_id": "b-a1"},
            )
        )
        self.assertTrue(root_user["ok"])
        self.assertTrue(root_assistant["ok"])
        self.assertTrue(short_reply["ok"])

        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(conversation_id="conv-branch", session_id="sess-branch", thread_id="thread-branch")
        )
        latest_turn = hydrate["recent_turns"][-1]
        resolution = latest_turn["metadata"]["resolution"]
        self.assertEqual(resolution["resolved_message_id"], "b-a1")
        self.assertEqual(hydrate["summary"]["latest_user_intent"]["effective_content"], "sure")
        self.assertEqual(hydrate["state"]["latest_user_ask"], "sure")
        self.assertEqual(latest_turn["metadata"]["reply_to_message_id"], "b-a1")
        self.assertEqual(hydrate["active_branch"]["reply_chain"][-1]["message_id"], "b-u2")
        self.assertEqual(hydrate["active_branch"]["reply_chain"][0]["message_id"], "b-u1")
        self.assertNotIn("b-a2", [turn["message_id"] for turn in hydrate["active_branch"]["turns"]])

    def test_provider_embedding_takes_precedence_when_configured(self) -> None:
        with mock.patch.object(embedding_engine.config, "BRAIN_EMBED_MODEL_PROVIDER", "openai"), \
             mock.patch.object(embedding_engine.config, "BRAIN_EMBED_MODEL_LOCAL", "simple"), \
             mock.patch("brain.runtime.memory.embedding_engine._provider_embedding", return_value=([0.25, 0.5], {"provider_id": "openai", "model": "test-model"})) as provider_mock:
            vector = embedding_engine.generate_embedding("hello world")

        self.assertEqual(vector, [0.25, 0.5])
        provider_mock.assert_called_once()

    def test_vector_fallback_returns_canonical_references(self) -> None:
        conn = store.connect()
        try:
            conn.execute(
                "INSERT INTO memory_index (source, confidence, metadata_json, content, schema_version) VALUES (?, ?, ?, ?, ?)",
                ("knowledge:7", 0.9, json.dumps({}), "alpha fallback", store.SCHEMA_VERSION),
            )
            conn.commit()
        finally:
            conn.close()

        results = vector_index.search_memory("alpha", limit=5)
        self.assertEqual(results[0]["entry_id"], "knowledge:7")
        self.assertEqual(results[0]["source_type"], "knowledge")
        self.assertEqual(results[0]["source_id"], "7")

    def test_distill_handles_sqlite_rows(self) -> None:
        conn = store.connect()
        try:
            conn.execute(
                "INSERT INTO experiences (task_id, outcome, reward_score, confidence, experience_type, source_module, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("t1", "Ship the durable queue fix", None, 1.0, "ingest", "test", store.SCHEMA_VERSION),
            )
            conn.commit()
        finally:
            conn.close()

        results = distill.distill_experiences(limit=5)
        self.assertEqual(len(results), 1)
        self.assertIn("candidate_id", results[0])

    def test_conversation_turn_dedupes_replayed_message_ids(self) -> None:
        first = app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="assistant",
                content="I will keep this durable.",
                session_id="sess-dedupe",
                thread_id="thread-dedupe",
                message_id="dup-1",
                timestamp="2026-03-15 13:00:00",
                metadata={"attempt": 1},
            )
        )
        second = app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="assistant",
                content="I will keep this durable.",
                session_id="sess-dedupe",
                thread_id="thread-dedupe",
                message_id="dup-1",
                timestamp="2026-03-15 13:00:00",
                metadata={"attempt": 2, "reply_to_message_id": "user-1"},
            )
        )

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(first["turn_id"], second["turn_id"])

        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(session_id="sess-dedupe", thread_id="thread-dedupe", turns_limit=10)
        )
        self.assertEqual(len(hydrate["recent_turns"]), 1)
        self.assertEqual(hydrate["recent_turns"][0]["message_id"], "dup-1")
        self.assertEqual(hydrate["recent_turns"][0]["metadata"]["attempt"], 2)
        self.assertEqual(hydrate["recent_turns"][0]["metadata"]["reply_to_message_id"], "user-1")

    def test_hydration_flags_latest_assistant_question_as_waiting_on_user(self) -> None:
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="Can you wire automatic hydration?",
                session_id="sess-question",
                thread_id="thread-question",
                message_id="q-u1",
                timestamp="2026-03-15 14:00:00",
            )
        )
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="assistant",
                content="Yes — should I checkpoint on compaction too?",
                session_id="sess-question",
                thread_id="thread-question",
                message_id="q-a1",
                timestamp="2026-03-15 14:00:01",
            )
        )

        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(session_id="sess-question", thread_id="thread-question", turns_limit=10)
        )
        open_loop_kinds = [item["kind"] for item in hydrate["summary"]["open_loops"]]
        pending_kinds = [item["kind"] for item in hydrate["summary"]["pending_actions"]]
        self.assertIn("awaiting_user_reply", open_loop_kinds)
        self.assertIn("await_user_clarification", pending_kinds)
        self.assertNotIn("assistant_commitment", open_loop_kinds)

    def test_optional_if_you_want_status_update_does_not_create_user_reply_loop(self) -> None:
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="assistant",
                content="All 3 are done. Docs, dry-run, and release checklist are in. If you want, I can tighten the open-loop heuristic next.",
                session_id="sess-optional",
                thread_id="thread-optional",
                message_id="o-a1",
                timestamp="2026-03-15 14:05:00",
            )
        )

        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(session_id="sess-optional", thread_id="thread-optional", turns_limit=10)
        )
        open_loop_kinds = [item["kind"] for item in hydrate["summary"]["open_loops"]]
        pending_kinds = [item["kind"] for item in hydrate["summary"]["pending_actions"]]
        self.assertNotIn("awaiting_user_reply", open_loop_kinds)
        self.assertNotIn("await_user_clarification", pending_kinds)

    def test_newer_assistant_turn_resolves_older_commitment_loop(self) -> None:
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="Please add automatic hydration.",
                session_id="sess-commit",
                thread_id="thread-commit",
                message_id="c-u1",
                timestamp="2026-03-15 14:10:00",
            )
        )
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="assistant",
                content="I will add automatic hydration next.",
                session_id="sess-commit",
                thread_id="thread-commit",
                message_id="c-a1",
                timestamp="2026-03-15 14:10:01",
            )
        )
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="assistant",
                content="Done — hydration now runs before prompt build.",
                session_id="sess-commit",
                thread_id="thread-commit",
                message_id="c-a2",
                timestamp="2026-03-15 14:10:02",
            )
        )

        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(session_id="sess-commit", thread_id="thread-commit", turns_limit=10)
        )
        open_loop_summaries = [item["summary"] for item in hydrate["summary"]["open_loops"]]
        self.assertNotIn("I will add automatic hydration next.", open_loop_summaries)
        self.assertIsNone(hydrate["summary"]["last_assistant_commitment"])
        self.assertIsNone(hydrate["state"]["last_assistant_commitment"])

    def test_ponder_timeout_wrapper_returns_without_waiting_for_hung_work(self) -> None:
        started = time.monotonic()
        result = pondering_engine._run_with_timeout(
            "timeout_test",
            lambda: (time.sleep(0.25), "late result")[1],
            0.05,
            {"fallback": True},
        )
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.30)
        self.assertEqual(result, {"fallback": True})

    def test_ponder_cycle_writes_reflections_from_continuity_candidates(self) -> None:
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="Remember to keep continuity hydrated across restarts.",
                session_id="sess-ponder",
                thread_id="thread-ponder",
                message_id="p-u1",
                timestamp="2026-03-15 15:00:00",
            )
        )
        checkpoint = app.conversation_checkpoint(
            app.ConversationCheckpointRequest(
                session_id="sess-ponder",
                thread_id="thread-ponder",
                checkpoint_kind="manual",
            )
        )
        self.assertTrue(checkpoint["ok"])
        api.store_memory("knowledge", "Continuity hydration should survive restarts.", source="test")

        with mock.patch("brain.runtime.memory.pondering_engine._infer_with_timeout", return_value={"status": "timeout", "output": ""}), \
             mock.patch.object(pondering_engine.config, "OCMEMOG_PONDER_ENABLED", "true"), \
             mock.patch.object(pondering_engine.config, "OCMEMOG_LESSON_MINING_ENABLED", "false"):
            result = pondering_engine.run_ponder_cycle(max_items=6)

        self.assertTrue(any(item["type"] in {"checkpoint", "memory", "continuity_state"} for item in result["insights"]))
        checkpoint_refs = [item.get("reference") for item in result["candidates"]]
        self.assertIn(f"conversation_checkpoints:{checkpoint['checkpoint']['id']}", checkpoint_refs)

        conn = store.connect()
        try:
            reflections = conn.execute("SELECT id, content, metadata_json FROM reflections ORDER BY id DESC").fetchall()
        finally:
            conn.close()
        self.assertGreaterEqual(len(reflections), 1)
        self.assertTrue(any("source_reference" in json.loads(row["metadata_json"] or "{}") for row in reflections))

        conn = store.connect()
        try:
            link_count = conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]
        finally:
            conn.close()
        self.assertGreaterEqual(int(link_count), 1)

    def test_hydrate_tolerates_state_refresh_timeout(self) -> None:
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="Keep the response path alive under queue pressure.",
                session_id="sess-degraded",
                thread_id="thread-degraded",
                message_id="d-u1",
                timestamp="2026-03-15 15:10:00",
            )
        )
        with mock.patch(
            "brain.runtime.memory.conversation_state._upsert_state",
            side_effect=TimeoutError("write queue timeout"),
        ):
            hydrate = app.conversation_hydrate(
                app.ConversationHydrateRequest(session_id="sess-degraded", thread_id="thread-degraded", turns_limit=6)
            )

        self.assertTrue(hydrate["ok"])
        self.assertEqual(hydrate["state"]["latest_user_ask"], "Keep the response path alive under queue pressure.")
        self.assertTrue(any("state refresh was delayed" in item for item in hydrate["warnings"]))

    def test_internal_continuity_wrapper_is_ignored_on_ingest(self) -> None:
        response = app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="Memory continuity (auto-hydrated by ocmemog):\n- Latest user ask: polluted\n- Recent turns: assistant: polluted",
                session_id="sess-ignore",
                thread_id="thread-ignore",
                message_id="ignore-u1",
                timestamp="2026-03-15 15:11:00",
            )
        )
        self.assertTrue(response["ok"])
        self.assertTrue(response.get("ignored"))

        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(session_id="sess-ignore", thread_id="thread-ignore", turns_limit=6)
        )
        self.assertTrue(hydrate["ok"])
        self.assertIsNone(hydrate["summary"]["latest_user_ask"])

    def test_hydrate_filters_internal_continuity_from_latest_user_ask_and_checkpoint(self) -> None:
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="Hello",
                session_id="sess-filter",
                thread_id="thread-filter",
                message_id="filter-u1",
                timestamp="2026-03-15 15:12:00",
            )
        )
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="assistant",
                content="What do you need?",
                session_id="sess-filter",
                thread_id="thread-filter",
                message_id="filter-a1",
                timestamp="2026-03-15 15:12:01",
            )
        )
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="Memory continuity (auto-hydrated by ocmemog):\n- Checkpoint: polluted\n- Latest user ask: polluted\n- Last assistant commitment: polluted",
                session_id="sess-filter",
                thread_id="thread-filter",
                message_id="filter-u2",
                timestamp="2026-03-15 15:12:02",
            )
        )

        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(session_id="sess-filter", thread_id="thread-filter", turns_limit=10)
        )
        self.assertEqual(hydrate["summary"]["latest_user_ask"]["content"], "Hello")

        checkpoint = app.conversation_checkpoint(
            app.ConversationCheckpointRequest(session_id="sess-filter", thread_id="thread-filter", checkpoint_kind="manual")
        )
        self.assertTrue(checkpoint["ok"])
        self.assertIn("user asked: Hello", checkpoint["checkpoint"]["summary"])
        self.assertNotIn("polluted", checkpoint["checkpoint"]["summary"])

    def test_ingest_normalizes_sender_envelope_and_reply_tag(self) -> None:
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="Sender (untrusted metadata):\n```json\n{\"label\":\"openclaw-tui\"}\n```\n\n[Tue 2026-03-17 19:10 EDT] Hello there",
                session_id="sess-normalize",
                thread_id="thread-normalize",
                message_id="normalize-u1",
                timestamp="2026-03-15 15:12:03",
            )
        )
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="assistant",
                content="[[reply_to_current]] I will fix this now.",
                session_id="sess-normalize",
                thread_id="thread-normalize",
                message_id="normalize-a1",
                timestamp="2026-03-15 15:12:04",
            )
        )
        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(session_id="sess-normalize", thread_id="thread-normalize", turns_limit=10)
        )
        self.assertEqual(hydrate["summary"]["latest_user_ask"]["content"], "Hello there")
        self.assertEqual(hydrate["summary"]["last_assistant_commitment"]["content"], "I will fix this now.")

    def test_ingest_keeps_only_latest_timestamp_message_from_polluted_wrapper(self) -> None:
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="- {\"label\":\"openclaw-tui\"} [Tue 2026-03-17 19:10 EDT] Hello - old assistant text [Tue 2026-03-17 19:30 EDT] ok",
                session_id="sess-timestamp",
                thread_id="thread-timestamp",
                message_id="timestamp-u1",
                timestamp="2026-03-15 15:12:04",
            )
        )
        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(session_id="sess-timestamp", thread_id="thread-timestamp", turns_limit=10)
        )
        self.assertEqual(hydrate["summary"]["latest_user_ask"]["content"], "ok")

    def test_refresh_state_self_heals_legacy_checkpoint_turns(self) -> None:
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="Hello",
                session_id="sess-heal",
                thread_id="thread-heal",
                message_id="heal-u1",
                timestamp="2026-03-15 15:12:05",
            )
        )
        checkpoint = app.conversation_checkpoint(
            app.ConversationCheckpointRequest(session_id="sess-heal", thread_id="thread-heal", checkpoint_kind="manual")
        )
        self.assertTrue(checkpoint["ok"])
        conn = store.connect()
        try:
            conn.execute(
                "INSERT INTO conversation_turns (conversation_id, session_id, thread_id, message_id, role, content, metadata_json, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (None, "sess-heal", "thread-heal", "heal-bad1", "user", "- Checkpoint: polluted Latest user ask: polluted", "{}", store.SCHEMA_VERSION),
            )
            conn.commit()
        finally:
            conn.close()

        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(session_id="sess-heal", thread_id="thread-heal", turns_limit=10)
        )
        self.assertEqual(hydrate["summary"]["latest_user_ask"]["content"], "Hello")
        latest_checkpoint = hydrate["summary"]["latest_checkpoint"]
        self.assertIsNotNone(latest_checkpoint)
        self.assertNotIn("polluted", latest_checkpoint["summary"])
        conn = store.connect()
        try:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM conversation_turns WHERE session_id=? AND content LIKE '- Checkpoint:%'",
                ("sess-heal",),
            ).fetchone()[0]
            bad_checkpoints = conn.execute(
                "SELECT COUNT(*) FROM conversation_checkpoints WHERE session_id=? AND summary LIKE '%polluted%'",
                ("sess-heal",),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(int(remaining), 0)
        self.assertEqual(int(bad_checkpoints), 0)

    def test_checkpoint_summary_with_oversized_assistant_commitment_is_treated_as_polluted(self) -> None:
        self.assertTrue(
            conversation_state._checkpoint_summary_is_polluted(
                "24 recent turns captured | user asked: ping | assistant committed: " + ("x" * 400)
            )
        )

    def test_vector_rebuild_does_not_break_mixed_conversation_flow(self) -> None:
        with mock.patch("brain.runtime.memory.embedding_engine.generate_embedding", side_effect=lambda text: [0.1, 0.2, float(len(text) % 7)]):
            for idx in range(18):
                api.store_memory("knowledge", f"vector rebuild seed {idx}", source="test")

            for idx in range(4):
                app.conversation_ingest_turn(
                    app.ConversationTurnRequest(
                        role="user" if idx % 2 == 0 else "assistant",
                        content=f"mixed flow turn {idx}",
                        conversation_id="conv-mixed",
                        session_id="sess-mixed",
                        thread_id="thread-mixed",
                        message_id=f"mixed-{idx}",
                        timestamp=f"2026-03-15 15:20:0{idx}",
                    )
                )

            checkpoint = app.conversation_checkpoint(
                app.ConversationCheckpointRequest(
                    conversation_id="conv-mixed",
                    session_id="sess-mixed",
                    thread_id="thread-mixed",
                    checkpoint_kind="manual",
                )
            )
            self.assertTrue(checkpoint["ok"])

            errors: list[str] = []

            def rebuild_task() -> None:
                vector_index.rebuild_vector_index()

            def hydrate_task() -> None:
                result = app.conversation_hydrate(
                    app.ConversationHydrateRequest(
                        conversation_id="conv-mixed",
                        session_id="sess-mixed",
                        thread_id="thread-mixed",
                        turns_limit=12,
                    )
                )
                if not result["ok"]:
                    raise AssertionError("hydrate returned not ok")

            def expand_task() -> None:
                result = app.conversation_checkpoint_expand(
                    app.ConversationCheckpointExpandRequest(
                        checkpoint_id=checkpoint["checkpoint"]["id"],
                        turns_limit=24,
                    )
                )
                if not result["ok"]:
                    raise AssertionError("checkpoint_expand returned not ok")

            def ponder_task() -> None:
                with mock.patch("brain.runtime.memory.pondering_engine._infer_with_timeout", return_value={"status": "timeout", "output": ""}), \
                     mock.patch.object(pondering_engine.config, "OCMEMOG_PONDER_ENABLED", "true"), \
                     mock.patch.object(pondering_engine.config, "OCMEMOG_LESSON_MINING_ENABLED", "false"):
                    result = app.memory_ponder(app.PonderRequest(max_items=4))
                if not result["ok"]:
                    raise AssertionError("ponder returned not ok")

            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(fn) for fn in (rebuild_task, hydrate_task, expand_task, ponder_task)]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:
                        errors.append(str(exc))

        self.assertEqual(errors, [])

    def test_restart_rehydrates_checkpoint_state_after_schema_reset(self) -> None:
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="Please resume the migration after restart.",
                conversation_id="conv-restart",
                session_id="sess-restart",
                thread_id="thread-restart",
                message_id="r-u1",
                timestamp="2026-03-15 16:00:00",
            )
        )
        app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="assistant",
                content="I will resume the migration and verify the checksum next.",
                conversation_id="conv-restart",
                session_id="sess-restart",
                thread_id="thread-restart",
                message_id="r-a1",
                timestamp="2026-03-15 16:00:01",
            )
        )
        follow_up = app.conversation_ingest_turn(
            app.ConversationTurnRequest(
                role="user",
                content="yes",
                conversation_id="conv-restart",
                session_id="sess-restart",
                thread_id="thread-restart",
                message_id="r-u2",
                timestamp="2026-03-15 16:00:02",
                metadata={"reply_to_message_id": "r-a1"},
            )
        )
        checkpoint = app.conversation_checkpoint(
            app.ConversationCheckpointRequest(
                conversation_id="conv-restart",
                session_id="sess-restart",
                thread_id="thread-restart",
                checkpoint_kind="manual",
            )
        )
        self.assertTrue(follow_up["ok"])
        self.assertTrue(checkpoint["ok"])

        store._SCHEMA_READY = False

        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(
                conversation_id="conv-restart",
                session_id="sess-restart",
                thread_id="thread-restart",
                turns_limit=10,
            )
        )
        self.assertTrue(hydrate["ok"])
        self.assertEqual(hydrate["state"]["latest_checkpoint_id"], checkpoint["checkpoint"]["id"])
        self.assertEqual(hydrate["state"]["latest_user_ask"], "yes")
        self.assertEqual(hydrate["checkpoint_graph"]["latest"]["id"], checkpoint["checkpoint"]["id"])

        expanded = app.conversation_turn_expand(
            app.ConversationTurnExpandRequest(turn_id=follow_up["turn_id"], radius_turns=3, turns_limit=12)
        )
        self.assertTrue(expanded["ok"])
        self.assertEqual(expanded["reply_chain"][-1]["message_id"], "r-u2")
        ranked_turn_ids = [item["turn"]["message_id"] for item in expanded["salience_ranked_turns"]]
        self.assertIn("r-u2", ranked_turn_ids[:3])
        ranked_checkpoint_ids = [item["checkpoint"]["id"] for item in expanded["salience_ranked_checkpoints"]]
        self.assertIn(checkpoint["checkpoint"]["id"], ranked_checkpoint_ids)

    def test_long_thread_ambiguity_stress_and_salience_ranked_expansion(self) -> None:
        turns = [
            ("user", "We need to finish recovery before noon.", "s-u1", None),
            ("assistant", "I can finish recovery first and benchmark continuity after.", "s-a1", None),
            ("assistant", "Alternatively I can tune dashboards later.", "s-a2", None),
            ("user", "What about the replay edge cases?", "s-u2", None),
            ("assistant", "I will cover replay edge cases in the restart suite.", "s-a3", None),
            ("user", "also check ambiguous replies in long threads", "s-u3", None),
            ("assistant", "Yes — should I anchor ambiguous replies to the exact proposal thread?", "s-a4", None),
            ("user", "sure", "s-u4", {"reply_to_message_id": "s-a4"}),
            ("assistant", "Great, I will anchor them and keep unrelated branches out of hydration.", "s-a5", None),
            ("assistant", "Unrelated branch: we could rewrite the UI labels later.", "s-a6", None),
            ("user", "ok", "s-u5", {"reply_to_message_id": "s-a5"}),
            ("assistant", "One more question: do you want checkpoint expansion ranked by salience too?", "s-a7", None),
            ("user", "yes", "s-u6", {"reply_to_message_id": "s-a7"}),
            ("assistant", "Understood — I will rank checkpoint and turn expansion by salience.", "s-a8", None),
        ]
        last_turn_id = None
        for offset, (role, content, message_id, metadata) in enumerate(turns):
            response = app.conversation_ingest_turn(
                app.ConversationTurnRequest(
                    role=role,
                    content=content,
                    conversation_id="conv-stress",
                    session_id="sess-stress",
                    thread_id="thread-stress",
                    message_id=message_id,
                    timestamp=f"2026-03-15 17:00:{offset:02d}",
                    metadata=metadata,
                )
            )
            self.assertTrue(response["ok"])
            last_turn_id = response["turn_id"]

        checkpoint = app.conversation_checkpoint(
            app.ConversationCheckpointRequest(
                conversation_id="conv-stress",
                session_id="sess-stress",
                thread_id="thread-stress",
                checkpoint_kind="manual",
                turns_limit=20,
            )
        )
        self.assertTrue(checkpoint["ok"])

        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(
                conversation_id="conv-stress",
                session_id="sess-stress",
                thread_id="thread-stress",
                turns_limit=20,
            )
        )
        self.assertIn("rank checkpoint and turn expansion by salience", hydrate["state"]["last_assistant_commitment"].lower())
        self.assertEqual(hydrate["summary"]["latest_user_intent"]["effective_content"], "yes")
        active_branch_ids = [turn["message_id"] for turn in hydrate["active_branch"]["turns"]]
        self.assertNotIn("s-a6", active_branch_ids)
        self.assertIn("s-u6", [turn["message_id"] for turn in hydrate["active_branch"]["reply_chain"]])

        checkpoint_expand = app.conversation_checkpoint_expand(
            app.ConversationCheckpointExpandRequest(
                checkpoint_id=checkpoint["checkpoint"]["id"],
                radius_turns=1,
                turns_limit=24,
            )
        )
        self.assertTrue(checkpoint_expand["ok"])
        self.assertEqual(checkpoint_expand["salience_ranked_turns"][0]["turn"]["message_id"], "s-u6")
        self.assertEqual(checkpoint_expand["salience_ranked_checkpoints"][0]["checkpoint"]["id"], checkpoint["checkpoint"]["id"])

        turn_expand = app.conversation_turn_expand(
            app.ConversationTurnExpandRequest(turn_id=last_turn_id, radius_turns=6, turns_limit=20)
        )
        self.assertTrue(turn_expand["ok"])
        self.assertEqual(turn_expand["salience_ranked_turns"][0]["turn"]["message_id"], "s-u6")
        self.assertIn("s-a7", [item["message_id"] for item in turn_expand["reply_chain"]])
        self.assertIn(checkpoint["checkpoint"]["id"], [item["id"] for item in turn_expand["related_checkpoints"]])

    def test_memory_get_hydrates_ingested_memory_provenance(self) -> None:
        transcript = Path(self.tempdir.name) / "prov.log"
        transcript.write_text("first\nsecond\nthird\n", encoding="utf-8")

        response = app.memory_ingest(
            app.IngestRequest(
                content="Durable summary of the shipping plan.",
                kind="memory",
                memory_type="knowledge",
                source="unit-test",
                conversation_id="conv-prov",
                session_id="sess-prov",
                thread_id="thread-prov",
                message_id="msg-prov",
                role="assistant",
                source_label="daily-summary",
                transcript_path=str(transcript),
                transcript_offset=2,
                transcript_end_offset=3,
            )
        )
        self.assertTrue(response["ok"])

        fetched = app.memory_get(app.GetRequest(reference=response["reference"]))
        self.assertTrue(fetched["ok"])
        memory = fetched["memory"]
        preview = memory["provenance_preview"]
        self.assertIn("conversation_turns:", memory["metadata"]["provenance"]["source_references"][0])
        self.assertEqual(preview["source_labels"], ["daily-summary"])
        self.assertEqual(preview["conversation"]["thread_id"], "thread-prov")
        outbound_targets = {item["target_reference"] for item in memory["provenance"]["outbound"]}
        self.assertIn("thread:thread-prov", outbound_targets)
        self.assertTrue(any(target.startswith("transcript:") for target in outbound_targets))

    def test_distill_and_promote_keep_structured_provenance(self) -> None:
        transcript = Path(self.tempdir.name) / "experience.log"
        transcript.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

        ingest = app.memory_ingest(
            app.IngestRequest(
                content="Capture the queue migration status and anchor it to the transcript.",
                kind="experience",
                task_id="task-prov",
                source="unit-test",
                source_reference="conversation_checkpoints:9",
                source_labels=["queue", "migration"],
                conversation_id="conv-exp",
                session_id="sess-exp",
                thread_id="thread-exp",
                message_id="msg-exp",
                transcript_path=str(transcript),
                transcript_offset=1,
                transcript_end_offset=2,
            )
        )
        self.assertTrue(ingest["ok"])

        distilled = distill.distill_experiences(limit=5)
        self.assertEqual(len(distilled), 1)
        candidate = distill.candidate.get_candidate(distilled[0]["candidate_id"])
        self.assertIsNotNone(candidate)
        candidate_payload = dict(candidate or {})
        candidate_payload["metadata"] = json.loads(candidate_payload.get("metadata_json") or "{}")

        promoted = promote.promote_candidate(candidate_payload)
        self.assertEqual(promoted["decision"], "promote")

        fetched = app.memory_get(app.GetRequest(reference=f"{promoted['destination']}:1"))
        self.assertTrue(fetched["ok"])
        memory = fetched["memory"]
        preview = memory["provenance_preview"]
        self.assertIn("experiences:1", preview["source_references"])
        self.assertIn("conversation_checkpoints:9", preview["source_references"])
        self.assertEqual(preview["source_labels"], ["queue", "migration", "unit-test"])
        outbound_targets = {item["target_reference"] for item in memory["provenance"]["outbound"]}
        self.assertIn("experiences:1", outbound_targets)
        self.assertIn("conversation_checkpoints:9", outbound_targets)
        self.assertIn("label:queue", outbound_targets)
        self.assertTrue(any(target.startswith("transcript:") for target in outbound_targets))

    def test_ponder_reflection_inherits_upstream_source_references(self) -> None:
        memory_response = app.memory_ingest(
            app.IngestRequest(
                content="Need to keep restart checkpoints tied to transcript anchors.",
                kind="memory",
                memory_type="knowledge",
                source="unit-test",
                source_reference="conversation_turns:42",
                source_labels=["restart"],
            )
        )
        self.assertTrue(memory_response["ok"])

        with mock.patch("brain.runtime.memory.pondering_engine._infer_with_timeout", return_value={"status": "ok", "output": "Insight: Preserve lineage\nRecommendation: Keep source anchors"}), \
             mock.patch.object(pondering_engine.config, "OCMEMOG_PONDER_ENABLED", "true"), \
             mock.patch.object(pondering_engine.config, "OCMEMOG_LESSON_MINING_ENABLED", "false"):
            result = pondering_engine.run_ponder_cycle(max_items=4)

        reflection_ref = next(item["reflection_reference"] for item in result["insights"] if item.get("reflection_reference", "").startswith("reflections:"))
        fetched = app.memory_get(app.GetRequest(reference=reflection_ref))
        self.assertTrue(fetched["ok"])
        preview = fetched["memory"]["provenance_preview"]
        self.assertIn(memory_response["reference"], preview["source_references"])
        self.assertIn("conversation_turns:42", preview["source_references"])


if __name__ == "__main__":
    unittest.main()
