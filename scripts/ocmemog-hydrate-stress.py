#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import uuid
from itertools import cycle
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import request as urlrequest

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17921


def _post_json(base_url: str, path: str, payload: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
    req = urlrequest.Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    try:
        return json.loads(body)
    except Exception:
        return {"ok": False, "raw": body}


def _get_json(base_url: str, path: str, *, timeout: float = 10.0) -> dict[str, Any]:
    req = urlrequest.Request(f"{base_url.rstrip('/')}{path}", method="GET")
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    try:
        return json.loads(body)
    except Exception:
        return {"ok": False, "raw": body}


def _sanitize_continuity_noise(text: str, max_len: int = 280) -> str:
    markers = [
        "Memory continuity (auto-hydrated by ocmemog):",
        "Pre-compaction memory flush.",
        "Current time:",
        "Latest user ask:",
        "Last assistant commitment:",
        "Open loops:",
        "Pending actions:",
        "Recent turns:",
        "Linked memories:",
        "Sender (untrusted metadata):",
    ]
    cleaned = text or ""
    for marker in markers:
        cleaned = cleaned.replace(marker, " ")
    cleaned = " ".join(cleaned.split()).strip()
    if len(cleaned) > max_len:
        cleaned = f"{cleaned[: max_len - 1].rstrip()}…"
    return cleaned


def _first_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _summarize_list(items: Any, limit: int = 3) -> list[str]:
    if not isinstance(items, list):
        return []
    output: list[str] = []
    for item in items[:limit]:
        record = _as_dict(item)
        text = _first_str(record.get("summary"), record.get("content"), record.get("reference"))
        if text:
            output.append(text)
    return output


def build_predictive_brief_context(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return ""
    brief = _as_dict(payload.get("predictive_brief"))
    if not brief:
        return ""
    lines: list[str] = []
    lane = _sanitize_continuity_noise(_first_str(brief.get("lane")), 48)
    if lane:
        lines.append(f"Lane: {lane}")
    checkpoint = _as_dict(brief.get("checkpoint"))
    checkpoint_summary = _sanitize_continuity_noise(_first_str(checkpoint.get("summary")), 140)
    if checkpoint_summary:
        lines.append(f"Checkpoint: {checkpoint_summary}")
    memories = brief.get("memories") if isinstance(brief.get("memories"), list) else []
    memory_lines = []
    for item in memories[:4]:
        record = _as_dict(item)
        text = _sanitize_continuity_noise(_first_str(record.get("content"), record.get("reference")), 120)
        if text:
            memory_lines.append(text)
    if memory_lines:
        lines.append(f"Likely-needed facts: {' | '.join(memory_lines)}")
    open_loops = brief.get("open_loops") if isinstance(brief.get("open_loops"), list) else []
    open_loop_lines = []
    for item in open_loops[:2]:
        record = _as_dict(item)
        text = _sanitize_continuity_noise(_first_str(record.get("summary"), record.get("reference")), 100)
        if text:
            open_loop_lines.append(text)
    if open_loop_lines:
        lines.append(f"Open loops: {' | '.join(open_loop_lines)}")
    if not lines:
        return ""
    joined = "\n- ".join(lines)
    return f"Working memory brief (JIT by ocmemog):\n- {joined}"


def build_hydration_context(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return ""
    summary = _as_dict(payload.get("summary"))
    state = _as_dict(payload.get("state"))
    lines: list[str] = []
    checkpoint = _as_dict(summary.get("latest_checkpoint"))
    checkpoint_summary = _sanitize_continuity_noise(_first_str(checkpoint.get("summary")), 140)
    if checkpoint_summary:
        lines.append(f"Checkpoint: {checkpoint_summary}")
    latest_user_ask = _as_dict(summary.get("latest_user_ask"))
    latest_user_text = _sanitize_continuity_noise(
        _first_str(latest_user_ask.get("effective_content"), latest_user_ask.get("content"), state.get("latest_user_ask")),
        220,
    )
    if latest_user_text:
        lines.append(f"Latest user ask: {latest_user_text}")
    commitment = _as_dict(summary.get("last_assistant_commitment"))
    commitment_text = _sanitize_continuity_noise(
        _first_str(commitment.get("content"), state.get("last_assistant_commitment")),
        180,
    )
    if commitment_text:
        lines.append(f"Last assistant commitment: {commitment_text}")
    open_loops = [_sanitize_continuity_noise(item, 120) for item in _summarize_list(summary.get("open_loops"), 2)]
    open_loops = [item for item in open_loops if item]
    if open_loops:
        lines.append(f"Open loops: {' | '.join(open_loops)}")
    if not lines:
        return ""
    joined = "\n- ".join(lines)
    return f"Memory continuity (auto-hydrated by ocmemog):\n- {joined}"


@dataclass
class HarnessResult:
    name: str
    ok: bool
    metrics: dict[str, Any]
    failures: list[str]


class SidecarHarness:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.base_url = f"http://{args.host}:{args.port}"
        self.tempdir = tempfile.TemporaryDirectory(prefix="ocmemog-hydrate-stress-")
        self.root = Path(self.tempdir.name)
        self.state_dir = self.root / "state"
        self.session_dir = self.root / "sessions"
        self.transcript_dir = self.root / "transcripts"
        self.logs_dir = self.root / "logs"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.out_log = self.logs_dir / "sidecar.out.log"
        self.err_log = self.logs_dir / "sidecar.err.log"
        self.process: subprocess.Popen[str] | None = None
        self.session_file = self.session_dir / f"{uuid.uuid4()}.jsonl"
        self.stop_event = threading.Event()

    def start(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "PYTHONPATH": str(REPO_ROOT),
                "OCMEMOG_STATE_DIR": str(self.state_dir),
                "OCMEMOG_SESSION_DIR": str(self.session_dir),
                "OCMEMOG_TRANSCRIPT_DIR": str(self.transcript_dir),
                "OCMEMOG_TRANSCRIPT_WATCHER": "true" if self.args.watcher else "false",
                "OCMEMOG_TRANSCRIPT_POLL_SECONDS": str(self.args.poll_seconds),
                "OCMEMOG_INGEST_BATCH_SECONDS": str(self.args.batch_seconds),
                "OCMEMOG_INGEST_BATCH_MAX": str(self.args.batch_max),
                "OCMEMOG_INGEST_ENDPOINT": f"{self.base_url}/memory/ingest_async",
                "OCMEMOG_TURN_INGEST_ENDPOINT": f"{self.base_url}/conversation/ingest_turn",
                "OCMEMOG_REINFORCE_SENTIMENT": "false",
                "OCMEMOG_SEARCH_SKIP_EMBEDDING_PROVIDER": "true",
                "OCMEMOG_TRACE_HYDRATE": "true" if self.args.trace else "false",
                "OCMEMOG_TRACE_HYDRATE_WARN_MS": str(self.args.trace_hydrate_warn_ms),
                "OCMEMOG_TRACE_REFRESH_STATE": "true" if self.args.trace else "false",
                "OCMEMOG_TRACE_REFRESH_STATE_WARN_MS": str(self.args.trace_refresh_warn_ms),
                "OCMEMOG_TRACE_WATCHER_TURN": "true" if self.args.trace else "false",
                "OCMEMOG_TRACE_WATCHER_TURN_WARN_MS": str(self.args.trace_watcher_turn_warn_ms),
            }
        )
        out = self.out_log.open("w", encoding="utf-8")
        err = self.err_log.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "ocmemog.sidecar.app:app",
                "--host",
                self.args.host,
                "--port",
                str(self.args.port),
            ],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=out,
            stderr=err,
            text=True,
        )
        deadline = time.time() + self.args.start_timeout
        last_error = None
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"sidecar exited early with code {self.process.returncode}")
            try:
                payload = _get_json(self.base_url, "/healthz", timeout=2.0)
                if payload.get("ok"):
                    return
            except Exception as exc:
                last_error = exc
            time.sleep(0.25)
        raise RuntimeError(f"sidecar did not become healthy before timeout: {last_error}")

    def stop(self) -> None:
        self.stop_event.set()
        if not self.process:
            if not self.args.keep_temp:
                self.tempdir.cleanup()
            return
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if not self.args.keep_temp:
            self.tempdir.cleanup()

    def sample_process(self) -> dict[str, float]:
        if not self.process or self.process.poll() is not None:
            return {"cpu": 0.0, "rss_kb": 0.0}
        try:
            output = subprocess.check_output(
                ["ps", "-p", str(self.process.pid), "-o", "%cpu=,rss="], text=True
            ).strip()
            parts = output.split()
            cpu = float(parts[0]) if parts else 0.0
            rss = float(parts[1]) if len(parts) > 1 else 0.0
            return {"cpu": cpu, "rss_kb": rss}
        except Exception:
            return {"cpu": 0.0, "rss_kb": 0.0}

    def report_sizes(self) -> dict[str, int]:
        report = self.state_dir / "reports" / "brain_memory.log.jsonl"
        watcher_errors = self.state_dir / "reports" / "ocmemog_transcript_watcher_errors.jsonl"
        return {
            "report_log_bytes": report.stat().st_size if report.exists() else 0,
            "watcher_error_log_bytes": watcher_errors.stat().st_size if watcher_errors.exists() else 0,
        }

    def read_trace_summary(self) -> dict[str, Any]:
        pattern = re.compile(r"^\[ocmemog\]\[(?P<group>route|state|watcher)\]\s+(?P<name>[a-zA-Z_]+)\s+elapsed_ms=(?P<elapsed>[0-9.]+).*$")
        summary: dict[str, dict[str, Any]] = {}
        if not self.err_log.exists():
            return {}
        try:
            lines = self.err_log.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return {}
        for line in lines:
            match = pattern.match(line.strip())
            if not match:
                continue
            key = f"{match.group('group')}.{match.group('name')}"
            elapsed = float(match.group("elapsed"))
            bucket = summary.setdefault(key, {"count": 0, "max_ms": 0.0, "avg_ms": 0.0, "total_ms": 0.0})
            bucket["count"] += 1
            bucket["total_ms"] += elapsed
            bucket["max_ms"] = max(float(bucket["max_ms"]), elapsed)
        for bucket in summary.values():
            count = int(bucket["count"])
            bucket["avg_ms"] = round(float(bucket["total_ms"]) / count, 3) if count else 0.0
            bucket["max_ms"] = round(float(bucket["max_ms"]), 3)
            bucket.pop("total_ms", None)
        return summary

    def append_session_message(self, role: str, content: str, *, message_id: str, parent_id: str | None = None) -> None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        record = {
            "type": "message",
            "id": message_id,
            "parentId": parent_id,
            "timestamp": stamp,
            "message": {"role": role, "content": [{"type": "text", "text": content}]},
        }
        with self.session_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def seed_conversation(self, turns: int) -> dict[str, str]:
        session_id = f"stress-sess-{uuid.uuid4().hex[:8]}"
        thread_id = f"stress-thread-{uuid.uuid4().hex[:8]}"
        conversation_id = f"stress-conv-{uuid.uuid4().hex[:8]}"
        previous = None
        for idx in range(turns):
            role = "user" if idx % 2 == 0 else "assistant"
            content = (
                f"user turn {idx}: keep the continuity state compact and stable under load"
                if role == "user"
                else f"assistant turn {idx}: acknowledged, keeping track of the task and next step"
            )
            self.append_session_message(role, content, message_id=f"seed-{idx}", parent_id=previous)
            previous = f"seed-{idx}"
            _post_json(
                self.base_url,
                "/conversation/ingest_turn",
                {
                    "role": role,
                    "content": content,
                    "session_id": session_id,
                    "thread_id": thread_id,
                    "conversation_id": conversation_id,
                    "message_id": previous,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                timeout=15.0,
            )
        return {"session_id": session_id, "thread_id": thread_id, "conversation_id": conversation_id}

    def seed_from_fixture(self, fixture_path: Path, scenario_name: str | None = None) -> dict[str, str]:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        scenarios = payload.get("scenarios") if isinstance(payload.get("scenarios"), list) else []
        if not scenarios:
            raise ValueError(f"fixture has no scenarios: {fixture_path}")
        scenario = None
        if scenario_name:
            for item in scenarios:
                if isinstance(item, dict) and item.get("name") == scenario_name:
                    scenario = item
                    break
            if scenario is None:
                raise ValueError(f"scenario not found: {scenario_name}")
        else:
            scenario = scenarios[0]
        scope = scenario.get("scope") if isinstance(scenario.get("scope"), dict) else {}
        if not scope:
            raise ValueError(f"scenario missing scope: {scenario_name or scenario.get('name')}")
        previous = None
        for idx, turn in enumerate(scenario.get("turns") or []):
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role") or "user")
            content = str(turn.get("content") or "").strip()
            if not content:
                continue
            message_id = str(turn.get("message_id") or f"fixture-{idx}")
            metadata = turn.get("metadata") if isinstance(turn.get("metadata"), dict) else {}
            parent_id = metadata.get("reply_to_message_id") or previous
            self.append_session_message(role, content, message_id=message_id, parent_id=parent_id)
            _post_json(
                self.base_url,
                "/conversation/ingest_turn",
                {
                    "role": role,
                    "content": content,
                    "conversation_id": scope.get("conversation_id"),
                    "session_id": scope.get("session_id"),
                    "thread_id": scope.get("thread_id"),
                    "message_id": message_id,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "metadata": metadata,
                },
                timeout=15.0,
            )
            previous = message_id
        return {
            "session_id": str(scope.get("session_id") or ""),
            "thread_id": str(scope.get("thread_id") or ""),
            "conversation_id": str(scope.get("conversation_id") or ""),
        }


def run_hydrate_calls(base_url: str, scope: dict[str, str], *, total_calls: int, concurrency: int, timeout: float, plugin_sim: bool) -> dict[str, Any]:
    latencies: list[float] = []
    failures: list[str] = []
    prepend_sizes: list[int] = []
    warning_count = 0

    def _one(_: int) -> None:
        nonlocal warning_count
        started = time.perf_counter()
        payload = _post_json(
            base_url,
            "/conversation/hydrate",
            {
                **scope,
                "turns_limit": 8,
                "memory_limit": 4,
            },
            timeout=timeout,
        )
        latencies.append((time.perf_counter() - started) * 1000.0)
        if not payload.get("ok"):
            failures.append(str(payload))
            return
        warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
        warning_count += len(warnings)
        if plugin_sim:
            prepend = "\n\n".join(
                part for part in [build_predictive_brief_context(payload), build_hydration_context(payload)] if part
            )
            prepend_sizes.append(len(prepend.encode("utf-8")))

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(_one, idx) for idx in range(total_calls)]
        for future in as_completed(futures):
            future.result()

    ordered = sorted(latencies)
    p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)] if ordered else 0.0
    return {
        "calls": total_calls,
        "failures": failures,
        "warning_count": warning_count,
        "avg_ms": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
        "p95_ms": round(p95, 3),
        "max_ms": round(max(latencies), 3) if latencies else 0.0,
        "prepend_sizes": prepend_sizes,
    }


def run_mode(args: argparse.Namespace, harness: SidecarHarness) -> HarnessResult:
    if args.fixture:
        scope = harness.seed_from_fixture(Path(args.fixture), args.scenario)
    else:
        scope = harness.seed_conversation(args.seed_turns)
    metrics: dict[str, Any] = {"mode": args.mode, "scope": scope}
    failures: list[str] = []

    report_before = harness.report_sizes()
    cpu_samples: list[float] = []
    rss_samples: list[float] = []

    def _sampler() -> None:
        while not harness.stop_event.is_set():
            sample = harness.sample_process()
            cpu_samples.append(sample["cpu"])
            rss_samples.append(sample["rss_kb"])
            time.sleep(args.sample_interval)

    sampler_thread = threading.Thread(target=_sampler, daemon=True)
    sampler_thread.start()

    try:
        if args.mode in {"watcher-only", "combined"}:
            previous = "seed-final"
            role_cycle = cycle(["user", "assistant"])
            runtime_templates = [
                "verify hydrate and watcher remain stable under concurrent load",
                "preserve branch specificity and avoid unrelated continuity noise",
                "keep checkpoint expansion bounded and relevant",
                "watch for CPU spikes and queue churn under synthetic pressure",
            ]
            for idx in range(args.turn_count):
                role = next(role_cycle)
                text = f"runtime {role} turn {idx}: {runtime_templates[idx % len(runtime_templates)]}"
                harness.append_session_message(role, text, message_id=f"runtime-{idx}", parent_id=previous)
                previous = f"runtime-{idx}"
                time.sleep(max(0.0, args.turn_interval_ms / 1000.0))

        hydrate_metrics = {}
        if args.mode in {"hydrate-only", "combined", "plugin-sim"}:
            hydrate_metrics = run_hydrate_calls(
                harness.base_url,
                scope,
                total_calls=args.hydrate_calls,
                concurrency=args.hydrate_concurrency,
                timeout=args.request_timeout,
                plugin_sim=args.mode == "plugin-sim" or args.mode == "combined",
            )
            if hydrate_metrics["failures"]:
                failures.extend(hydrate_metrics["failures"][:5])
            if hydrate_metrics.get("p95_ms", 0.0) > args.max_p95_ms:
                failures.append(f"hydrate p95 too high: {hydrate_metrics['p95_ms']}ms > {args.max_p95_ms}ms")
            if hydrate_metrics.get("prepend_sizes"):
                max_prepend = max(hydrate_metrics["prepend_sizes"])
                if max_prepend > args.max_prepend_bytes:
                    failures.append(f"prepend too large: {max_prepend} > {args.max_prepend_bytes} bytes")
            metrics["hydrate"] = hydrate_metrics

        time.sleep(args.settle_seconds)
        health = _get_json(harness.base_url, "/healthz", timeout=5.0)
        metrics["health"] = health
        if not health.get("ok"):
            failures.append(f"healthz not ok: {health}")
    finally:
        harness.stop_event.set()
        sampler_thread.join(timeout=2)

    report_after = harness.report_sizes()
    metrics["process"] = {
        "cpu_peak": round(max(cpu_samples), 3) if cpu_samples else 0.0,
        "cpu_avg": round(sum(cpu_samples) / len(cpu_samples), 3) if cpu_samples else 0.0,
        "rss_peak_kb": round(max(rss_samples), 3) if rss_samples else 0.0,
    }
    metrics["trace_summary"] = harness.read_trace_summary() if args.trace else {}
    metrics["log_growth"] = {
        key: report_after.get(key, 0) - report_before.get(key, 0)
        for key in set(report_before) | set(report_after)
    }

    if metrics["process"]["cpu_peak"] > args.max_cpu_peak:
        failures.append(f"cpu peak too high: {metrics['process']['cpu_peak']} > {args.max_cpu_peak}")
    if metrics["log_growth"]["report_log_bytes"] > args.max_report_log_growth_bytes:
        failures.append(
            f"report log grew too fast: {metrics['log_growth']['report_log_bytes']} > {args.max_report_log_growth_bytes}"
        )
    if metrics["log_growth"]["watcher_error_log_bytes"] > args.max_watcher_error_growth_bytes:
        failures.append(
            f"watcher error log grew too fast: {metrics['log_growth']['watcher_error_log_bytes']} > {args.max_watcher_error_growth_bytes}"
        )

    return HarnessResult(name=args.mode, ok=not failures, metrics=metrics, failures=failures)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gateway-independent stress harness for ocmemog hydration/watcher interactions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            Modes:
              hydrate-only  Repeated /conversation/hydrate calls against seeded state
              watcher-only  Session-jsonl append workload with watcher enabled
              combined      Watcher append workload plus concurrent hydrate requests
              plugin-sim    Hydrate calls plus plugin-style prepend formatting budget checks
            """
        ),
    )
    parser.add_argument("--mode", choices=["hydrate-only", "watcher-only", "combined", "plugin-sim"], default="combined")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--seed-turns", type=int, default=12)
    parser.add_argument("--fixture", default="", help="Optional path to a fixture JSON file with scenarios")
    parser.add_argument("--scenario", default="", help="Optional scenario name inside the fixture file")
    parser.add_argument("--turn-count", type=int, default=120)
    parser.add_argument("--turn-interval-ms", type=float, default=25.0)
    parser.add_argument("--hydrate-calls", type=int, default=60)
    parser.add_argument("--hydrate-concurrency", type=int, default=2)
    parser.add_argument("--watcher", action="store_true", default=False)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--batch-seconds", type=float, default=0.5)
    parser.add_argument("--batch-max", type=int, default=8)
    parser.add_argument("--sample-interval", type=float, default=0.5)
    parser.add_argument("--settle-seconds", type=float, default=1.0)
    parser.add_argument("--request-timeout", type=float, default=30.0)
    parser.add_argument("--start-timeout", type=float, default=20.0)
    parser.add_argument("--max-cpu-peak", type=float, default=85.0)
    parser.add_argument("--max-p95-ms", type=float, default=2500.0)
    parser.add_argument("--max-report-log-growth-bytes", type=int, default=8_000_000)
    parser.add_argument("--max-watcher-error-growth-bytes", type=int, default=200_000)
    parser.add_argument("--max-prepend-bytes", type=int, default=12_000)
    parser.add_argument("--trace", action="store_true", help="Enable sidecar timing traces for hydrate/refresh/watcher turn paths")
    parser.add_argument("--keep-temp", action="store_true", help="Preserve temp state/logs for inspection")
    parser.add_argument("--trace-hydrate-warn-ms", type=float, default=25.0)
    parser.add_argument("--trace-refresh-warn-ms", type=float, default=15.0)
    parser.add_argument("--trace-watcher-turn-warn-ms", type=float, default=20.0)
    parser.add_argument("--json", action="store_true", help="Emit only JSON summary")
    args = parser.parse_args()
    if args.mode in {"watcher-only", "combined"}:
        args.watcher = True
    return args


def main() -> int:
    args = parse_args()
    harness = SidecarHarness(args)
    try:
        harness.start()
        result = run_mode(args, harness)
    finally:
        harness.stop()
    payload = {
        "ok": result.ok,
        "mode": result.name,
        "metrics": result.metrics,
        "failures": result.failures,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(json.dumps(payload, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
