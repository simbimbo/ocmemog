#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(REPO_ROOT))

from brain.runtime.memory import store  # noqa: E402
from ocmemog.sidecar import app  # noqa: E402


def _reset_runtime(state_dir: str) -> None:
    os.environ["OCMEMOG_STATE_DIR"] = state_dir
    os.environ["OCMEMOG_TRANSCRIPT_ROOTS"] = state_dir
    store._SCHEMA_READY = False
    app.QUEUE_STATS.update({
        "last_run": None,
        "processed": 0,
        "errors": 0,
        "last_error": None,
        "last_batch": 0,
    })


def _message_id_list(items: List[Dict[str, Any]]) -> List[str]:
    return [str(item.get("message_id") or "") for item in items if item.get("message_id")]


def _run_check(results: List[Dict[str, Any]], name: str, ok: bool, details: Dict[str, Any]) -> None:
    results.append({"name": name, "ok": bool(ok), "details": details})


def run_scenario(scenario: Dict[str, Any]) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as tempdir:
        _reset_runtime(tempdir)
        scope = dict(scenario.get("scope") or {})
        checkpoints = []
        message_to_turn_id: Dict[str, int] = {}

        for idx, turn in enumerate(scenario.get("turns") or []):
            response = app.conversation_ingest_turn(
                app.ConversationTurnRequest(
                    role=turn["role"],
                    content=turn["content"],
                    conversation_id=scope.get("conversation_id"),
                    session_id=scope.get("session_id"),
                    thread_id=scope.get("thread_id"),
                    message_id=turn.get("message_id"),
                    metadata=turn.get("metadata"),
                    timestamp=f"2026-03-15 18:00:{idx:02d}",
                )
            )
            if turn.get("message_id"):
                message_to_turn_id[str(turn["message_id"])] = int(response["turn_id"])
            if turn.get("message_id") in set(scenario.get("checkpoint_after") or []):
                checkpoints.append(
                    app.conversation_checkpoint(
                        app.ConversationCheckpointRequest(
                            conversation_id=scope.get("conversation_id"),
                            session_id=scope.get("session_id"),
                            thread_id=scope.get("thread_id"),
                            checkpoint_kind="benchmark",
                            turns_limit=32,
                        )
                    )["checkpoint"]
                )

        hydrate = app.conversation_hydrate(
            app.ConversationHydrateRequest(
                conversation_id=scope.get("conversation_id"),
                session_id=scope.get("session_id"),
                thread_id=scope.get("thread_id"),
                turns_limit=32,
            )
        )

        # Simulated restart/recovery: reset schema bootstrap and hydrate from persisted SQLite state.
        store._SCHEMA_READY = False
        recovered = app.conversation_hydrate(
            app.ConversationHydrateRequest(
                conversation_id=scope.get("conversation_id"),
                session_id=scope.get("session_id"),
                thread_id=scope.get("thread_id"),
                turns_limit=32,
            )
        )

        latest_checkpoint = checkpoints[-1] if checkpoints else None
        checkpoint_expand = None
        if latest_checkpoint:
            checkpoint_expand = app.conversation_checkpoint_expand(
                app.ConversationCheckpointExpandRequest(checkpoint_id=int(latest_checkpoint["id"]), turns_limit=48)
            )
        turn_expand = None
        if scenario.get("turn_expand_message_id"):
            turn_id = message_to_turn_id[str(scenario["turn_expand_message_id"])]
            turn_expand = app.conversation_turn_expand(
                app.ConversationTurnExpandRequest(turn_id=turn_id, radius_turns=8, turns_limit=48)
            )

        checks: List[Dict[str, Any]] = []
        expect = dict(scenario.get("expect") or {})
        latest_user_contains = str(expect.get("latest_user_contains") or "").strip()
        if latest_user_contains:
            value = str((hydrate.get("state") or {}).get("latest_user_ask") or "")
            recovered_value = str((recovered.get("state") or {}).get("latest_user_ask") or "")
            _run_check(checks, "hydrate.latest_user_contains", latest_user_contains in value, {"value": value, "expected": latest_user_contains})
            _run_check(checks, "restart.latest_user_contains", latest_user_contains in recovered_value, {"value": recovered_value, "expected": latest_user_contains})

        reply_chain_expected = list(expect.get("active_branch_reply_chain_contains") or [])
        if reply_chain_expected:
            reply_chain_ids = _message_id_list((hydrate.get("active_branch") or {}).get("reply_chain") or [])
            recovered_reply_chain_ids = _message_id_list((recovered.get("active_branch") or {}).get("reply_chain") or [])
            _run_check(checks, "hydrate.reply_chain_contains", all(item in reply_chain_ids for item in reply_chain_expected), {"value": reply_chain_ids, "expected": reply_chain_expected})
            _run_check(checks, "restart.reply_chain_contains", all(item in recovered_reply_chain_ids for item in reply_chain_expected), {"value": recovered_reply_chain_ids, "expected": reply_chain_expected})

        excluded = list(expect.get("active_branch_turns_exclude") or [])
        if excluded:
            branch_turn_ids = _message_id_list((hydrate.get("active_branch") or {}).get("turns") or [])
            _run_check(checks, "hydrate.active_branch_excludes", all(item not in branch_turn_ids for item in excluded), {"value": branch_turn_ids, "excluded": excluded})

        top_ranked_turn_message_id = str(expect.get("top_ranked_turn_message_id") or "").strip()
        if top_ranked_turn_message_id and checkpoint_expand:
            ranked = checkpoint_expand.get("salience_ranked_turns") or []
            top_id = str((((ranked[0] if ranked else {}).get("turn") or {}).get("message_id") or ""))
            _run_check(checks, "checkpoint_expand.top_ranked_turn", top_id == top_ranked_turn_message_id, {"value": top_id, "expected": top_ranked_turn_message_id})
        if top_ranked_turn_message_id and turn_expand:
            ranked = turn_expand.get("salience_ranked_turns") or []
            top_id = str((((ranked[0] if ranked else {}).get("turn") or {}).get("message_id") or ""))
            _run_check(checks, "turn_expand.top_ranked_turn", top_id == top_ranked_turn_message_id, {"value": top_id, "expected": top_ranked_turn_message_id})

        passed = sum(1 for item in checks if item["ok"])
        total = len(checks)
        score = 1.0 if total == 0 else round(passed / total, 3)
        return {
            "name": scenario.get("name"),
            "score": score,
            "passed": passed,
            "total": total,
            "ok": passed == total,
            "checks": checks,
            "checkpoint_id": latest_checkpoint.get("id") if latest_checkpoint else None,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default=str(REPO_ROOT / "tests" / "fixtures" / "continuity_benchmark.json"))
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    scenarios = [run_scenario(item) for item in fixture.get("scenarios") or []]
    overall_score = round(sum(item["score"] for item in scenarios) / max(len(scenarios), 1), 3)
    continuity_bar = float(fixture.get("continuity_bar", 1.0))
    report = {
        "ok": overall_score >= continuity_bar and all(item["ok"] for item in scenarios),
        "overall_score": overall_score,
        "continuity_bar": continuity_bar,
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
    }
    output = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        Path(args.report).write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
