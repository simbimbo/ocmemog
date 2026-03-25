from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ocmemog.sidecar import app

REPO_ROOT = Path(__file__).resolve().parent.parent


class HydrateStabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)

    def _seed_turns(self, *, session_id: str, thread_id: str, count: int = 8) -> None:
        for idx in range(count):
            role = "user" if idx % 2 == 0 else "assistant"
            content = (
                f"user turn {idx}: please keep continuity compact, useful, and stable"
                if role == "user"
                else f"assistant turn {idx}: acknowledged, next step remains focused and bounded"
            )
            app.conversation_ingest_turn(
                app.ConversationTurnRequest(
                    role=role,
                    content=content,
                    session_id=session_id,
                    thread_id=thread_id,
                    conversation_id=f"conv-{session_id}",
                    message_id=f"msg-{idx}",
                    timestamp=f"2026-03-25 09:{10 + idx:02d}:00",
                )
            )

    def test_hydrate_repeated_calls_remain_bounded(self) -> None:
        session_id = "sess-bounded"
        thread_id = "thread-bounded"
        self._seed_turns(session_id=session_id, thread_id=thread_id, count=10)

        prepend_sizes: list[int] = []
        latest_asks: list[str | None] = []
        open_loop_counts: list[int] = []

        for _ in range(40):
            hydrate = app.conversation_hydrate(
                app.ConversationHydrateRequest(
                    session_id=session_id,
                    thread_id=thread_id,
                    turns_limit=8,
                    memory_limit=4,
                )
            )
            self.assertTrue(hydrate["ok"])
            summary = hydrate.get("summary") or {}
            state = hydrate.get("state") or {}
            latest_user = (summary.get("latest_user_ask") or {}) if isinstance(summary.get("latest_user_ask"), dict) else {}
            latest_asks.append(latest_user.get("effective_content") or latest_user.get("content"))
            open_loop_counts.append(len(summary.get("open_loops") or []))
            continuity_lines = []
            if state.get("latest_user_ask"):
                continuity_lines.append(f"Latest user ask: {state['latest_user_ask']}")
            if state.get("last_assistant_commitment"):
                continuity_lines.append(f"Last assistant commitment: {state['last_assistant_commitment']}")
            if summary.get("open_loops"):
                continuity_lines.append(f"Open loops: {len(summary.get('open_loops') or [])}")
            prepend = "Memory continuity (auto-hydrated by ocmemog):\n- " + "\n- ".join(continuity_lines)
            prepend_sizes.append(len(prepend.encode("utf-8")))

        self.assertTrue(all(item == latest_asks[0] for item in latest_asks))
        self.assertLessEqual(max(prepend_sizes), 12_000)
        self.assertLessEqual(max(open_loop_counts), 10)

    def test_hydrate_returns_derived_state_without_inline_refresh(self) -> None:
        session_id = "sess-degraded-stability"
        thread_id = "thread-degraded-stability"
        self._seed_turns(session_id=session_id, thread_id=thread_id, count=6)

        with mock.patch("ocmemog.runtime.memory.conversation_state.refresh_state") as refresh_state:
            hydrate = app.conversation_hydrate(
                app.ConversationHydrateRequest(session_id=session_id, thread_id=thread_id, turns_limit=6)
            )

        refresh_state.assert_not_called()
        self.assertTrue(hydrate["ok"])
        self.assertEqual(
            (hydrate.get("state") or {}).get("latest_user_ask"),
            "user turn 4: please keep continuity compact, useful, and stable",
        )
        self.assertTrue(any("without inline state refresh" in warning for warning in hydrate.get("warnings", [])))

    def test_plugin_sim_prepend_budget_is_bounded(self) -> None:
        session_id = "sess-plugin-sim"
        thread_id = "thread-plugin-sim"
        self._seed_turns(session_id=session_id, thread_id=thread_id, count=10)

        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(
                session_id=session_id,
                thread_id=thread_id,
                turns_limit=8,
                memory_limit=4,
            )
        )
        self.assertTrue(hydrate["ok"])

        summary = hydrate.get("summary") or {}
        state = hydrate.get("state") or {}
        latest_user = summary.get("latest_user_ask") or {}
        commitment = summary.get("last_assistant_commitment") or {}
        checkpoint = summary.get("latest_checkpoint") or {}

        lines = []
        if checkpoint.get("summary"):
            lines.append(f"Checkpoint: {checkpoint['summary']}")
        latest_user_text = latest_user.get("effective_content") or latest_user.get("content") or state.get("latest_user_ask")
        if latest_user_text:
            lines.append(f"Latest user ask: {latest_user_text}")
        if commitment.get("content") or state.get("last_assistant_commitment"):
            lines.append(f"Last assistant commitment: {commitment.get('content') or state.get('last_assistant_commitment')}")
        prepend = "Memory continuity (auto-hydrated by ocmemog):\n- " + "\n- ".join(lines)

        self.assertLessEqual(len(prepend.encode("utf-8")), 12_000)
        self.assertIn("Latest user ask:", prepend)

    def test_stress_harness_combined_fixture_run(self) -> None:
        fixture = REPO_ROOT / "tests" / "fixtures" / "continuity_benchmark.json"
        result = subprocess.run(
            [
                str(REPO_ROOT / ".venv" / "bin" / "python"),
                str(REPO_ROOT / "scripts" / "ocmemog-hydrate-stress.py"),
                "--mode",
                "combined",
                "--fixture",
                str(fixture),
                "--scenario",
                "long_thread_ambiguity_salience",
                "--turn-count",
                "40",
                "--hydrate-calls",
                "20",
                "--hydrate-concurrency",
                "2",
                "--port",
                "17924",
                "--json",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + "\n" + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "combined")
        self.assertLessEqual(payload["metrics"]["process"]["cpu_peak"], 85.0)
        self.assertLessEqual(payload["metrics"]["hydrate"]["p95_ms"], 2500.0)
        self.assertTrue(payload["metrics"]["health"]["ok"])


if __name__ == "__main__":
    unittest.main()
