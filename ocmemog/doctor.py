"""Operator-facing diagnostics command for ocmemog runtime and sidecar state."""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
from collections.abc import Iterable
from dataclasses import dataclass, asdict
from typing import Any, Callable
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import contextlib

from ocmemog.runtime import state_store
from ocmemog.runtime.memory import embedding_engine, health, store
from ocmemog.sidecar import compat as sidecar_compat


@dataclass(frozen=True)
class FixResult:
    action: str
    check_key: str
    message: str
    changed: int
    ok: bool


@dataclass(frozen=True)
class CheckResult:
    key: str
    label: str
    status: str
    message: str
    details: dict[str, Any]
    fixable: bool = False
    fixed: bool = False
    fix_action: str | None = None
    fix_details: dict[str, Any] | None = None


@dataclass(frozen=True)
class DoctorCheck:
    key: str
    label: str
    check: Callable[[None], CheckResult]
    fix_key: str | None = None
    fix: Callable[[None], FixResult] | None = None


_STATUS_PRECEDENCE = {"fail": 2, "warn": 1, "ok": 0}


_ENV_TOGGLE_KEYS = (
    "OCMEMOG_TRANSCRIPT_WATCHER",
    "OCMEMOG_AUTO_HYDRATION",
    "OCMEMOG_INGEST_ASYNC_WORKER",
    "OCMEMOG_SHUTDOWN_DRAIN_QUEUE",
    "OCMEMOG_SHUTDOWN_TIMING",
    "OCMEMOG_SHUTDOWN_DUMP_THREADS",
    "OCMEMOG_USE_OLLAMA",
    "OCMEMOG_REINFORCE_SENTIMENT",
)
_SCHEMA_VERSION_NON_STANDARD_TABLES = {"artifacts", "vector_embeddings"}
_HTTP_TIMEOUT_SECONDS = 2.0


def _queue_backlog_severity(depth: int) -> str:
    if depth <= 0:
        return "none"
    if depth <= 25:
        return "low"
    if depth <= 250:
        return "medium"
    if depth <= 1000:
        return "high"
    return "critical"


def _vector_backlog_severity(missing: int) -> str:
    if missing <= 0:
        return "none"
    if missing <= 200:
        return "low"
    if missing <= 2000:
        return "medium"
    if missing <= 10000:
        return "high"
    return "critical"


def _parse_float_env(name: str, default: float, *, minimum: float | None = None) -> tuple[float, str | None]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default, None
    try:
        value = float(raw)
    except Exception:
        return default, f"{name} must be numeric"
    if minimum is not None and value < minimum:
        return default, f"{name} must be >= {minimum}"
    return value, None


def _parse_int_env(name: str, default: int, *, minimum: int | None = None) -> tuple[int, str | None]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default, None
    try:
        value = int(raw)
    except Exception:
        return default, f"{name} must be integer"
    if minimum is not None and value < minimum:
        return default, f"{name} must be >= {minimum}"
    return value, None


def _parse_bool_env(name: str, default: bool = False) -> tuple[bool, str | None]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default, None
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on", "y", "t"}:
        return True, None
    if lowered in {"0", "false", "no", "off", "n", "f"}:
        return False, None
    return default, f"{name} must be a boolean value"


def _queue_status_to_icon(status: str) -> str:
    if status == "fail":
        return "FAIL"
    if status == "warn":
        return "WARN"
    return "PASS"


def _normalize_fixes(raw: Iterable[str] | None) -> list[str]:
    actions: list[str] = []
    if not raw:
        return actions
    for item in raw:
        if not item:
            continue
        for part in item.split(","):
            part = part.strip()
            if part:
                actions.append(part)
    return sorted(dict.fromkeys(actions).keys())


@contextlib.contextmanager
def _scoped_state_dir(state_dir: str | None):
    if not state_dir:
        yield
        return
    previous = os.environ.get("OCMEMOG_STATE_DIR")
    os.environ["OCMEMOG_STATE_DIR"] = state_dir
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("OCMEMOG_STATE_DIR", None)
        else:
            os.environ["OCMEMOG_STATE_DIR"] = previous


def _run_imports(_: None) -> CheckResult:
    required_modules = (
        "ocmemog.runtime",
        "ocmemog.runtime.config",
        "ocmemog.runtime.memory",
        "ocmemog.runtime.memory.store",
        "ocmemog.runtime.memory.health",
        "ocmemog.runtime.memory.integrity",
        "ocmemog.runtime.memory.vector_index",
        "ocmemog.runtime.inference",
        "ocmemog.runtime.providers",
        "ocmemog.runtime.memory.embedding_engine",
        "ocmemog.sidecar.compat",
    )

    errors: list[str] = []
    for module_name in required_modules:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            errors.append(f"{module_name}: {exc}")

    if errors:
        return CheckResult(
            key="runtime/imports",
            label="runtime module imports",
            status="fail",
            message="Some required modules failed to import.",
            details={
                "tested": list(required_modules),
                "errors": errors,
            },
        )
    return CheckResult(
        key="runtime/imports",
        label="runtime module imports",
        status="ok",
        message="All runtime modules imported.",
        details={"tested": list(required_modules)},
    )


def _run_state_paths(_: None) -> CheckResult:
    targets = [state_store.root_dir(), state_store.data_dir(), state_store.memory_dir(), state_store.reports_dir()]
    failed: list[str] = []
    tested: list[str] = []
    for target in targets:
        tested.append(str(target))
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".ocmemog_doctor_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except Exception as exc:
            failed.append(f"{target}: {exc}")

    if failed:
        return CheckResult(
            key="state/path-writable",
            label="state path writability",
            status="fail",
            message="State directories are not fully writable.",
            details={"tested": tested, "failed": failed},
            fixable=True,
            fix_action="create-missing-paths",
        )
    return CheckResult(
        key="state/path-writable",
        label="state path writability",
        status="ok",
        message="State directories exist and are writable.",
        details={"tested": tested},
        fixable=True,
        fix_action="create-missing-paths",
    )


def _run_sqlite_schema(_: None) -> CheckResult:
    required = {
        "memory_events",
        "environment_cognition",
        "experiences",
        "directives",
        "candidates",
        "promotions",
        "demotions",
        "cold_storage",
        "memory_index",
        "vector_embeddings",
        "artifacts",
        "knowledge",
        "preferences",
        "identity",
        "runbooks",
        "lessons",
        "reflections",
        "tasks",
        "conversation_turns",
        "conversation_checkpoints",
        "conversation_state",
    } | set(store.MEMORY_TABLES)

    counts: dict[str, int] = {table: 0 for table in required}
    version_map: dict[str, dict[str, int]] = {}
    version_issues: list[str] = []
    try:
        store.init_db()
        conn = store.connect()
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            missing = sorted(required - tables)
            quick = str(conn.execute("PRAGMA quick_check(1)").fetchone()[0] or "unknown")
            for table in sorted(required):
                if table in missing:
                    continue
                try:
                    counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
                except Exception as exc:
                    version_issues.append(f"{table} row count query failed: {exc}")

                try:
                    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                    if "schema_version" not in columns:
                        if table not in _SCHEMA_VERSION_NON_STANDARD_TABLES:
                            version_issues.append(f"{table} missing schema_version column")
                        continue
                    rows = conn.execute(
                        f"SELECT COALESCE(schema_version, '<null>') AS schema_version, COUNT(*) AS count "
                        f"FROM {table} GROUP BY COALESCE(schema_version, '<null>')"
                    ).fetchall()
                    version_map[table] = {str(item[0]): int(item[1]) for item in rows}
                except Exception as exc:
                    version_issues.append(f"{table} schema query failed: {exc}")
        finally:
            conn.close()
    except Exception as exc:
        return CheckResult(
            key="sqlite/schema-access",
            label="sqlite and schema",
            status="fail",
            message=f"SQLite schema check failed: {exc}",
            details={"error": str(exc)},
        )
    if not missing:
        details = {
            "required_tables": sorted(required),
            "missing_tables": [],
            "sqlite_quick_check": quick,
            "row_counts": {key: counts[key] for key in sorted(counts)},
            "schema_version_expected": store.SCHEMA_VERSION,
            "schema_versions": version_map,
            "schema_version_issues": version_issues,
        }
    else:
        details = {
            "required_tables": sorted(required),
            "missing_tables": missing,
            "sqlite_quick_check": quick,
            "row_counts": {key: counts[key] for key in sorted(counts)},
            "schema_version_expected": store.SCHEMA_VERSION,
            "schema_versions": version_map,
            "schema_version_issues": version_issues,
        }
    if version_issues:
        details["schema_version_issues"] = version_issues
    for table, versions in version_map.items():
        unexpected = [item for item in versions if item != store.SCHEMA_VERSION]
        if unexpected and table not in ("memory_events", "environment_cognition"):
            version_issues.extend([f"{table} has unexpected schema_version value(s): {', '.join(sorted(unexpected))}"])

    if missing:
        return CheckResult(
            key="sqlite/schema-access",
            label="sqlite and schema",
            status="fail",
            message="One or more expected schema tables are missing.",
            details=details,
        )
    if quick.lower() != "ok":
        return CheckResult(
            key="sqlite/schema-access",
            label="sqlite and schema",
            status="fail",
            message="SQLite quick check failed.",
            details=details,
        )
    if version_issues:
        return CheckResult(
            key="sqlite/schema-access",
            label="sqlite and schema",
            status="warn",
            message="Schema metadata includes unexpected versions or schema column issues.",
            details=details,
        )
    return CheckResult(
        key="sqlite/schema-access",
        label="sqlite and schema",
        status="ok",
        message="SQLite schema and DB open state are healthy.",
        details=details,
    )


def _import_sidecar_app():
    return importlib.import_module("ocmemog.sidecar.app")


def _run_queue_health(_: None) -> CheckResult:
    try:
        app = _import_sidecar_app()
    except Exception as exc:
        return CheckResult(
            key="queue/health",
            label="queue health",
            status="fail",
            message=f"Failed to import sidecar app for queue checks: {exc}",
            details={"error": str(exc)},
            fixable=True,
            fix_action="repair-queue",
        )

    try:
        queue_path = app._queue_path()
        depth = app._queue_depth()
        stats = dict(app.QUEUE_STATS)
        queue_size = queue_path.stat().st_size
        worker_enabled = app._parse_bool_env("OCMEMOG_INGEST_ASYNC_WORKER", default=True)
        worker_poll_seconds = None
        worker_batch_max = None
        queue_config: list[str] = []
        try:
            worker_poll_seconds = float(os.environ.get("OCMEMOG_INGEST_ASYNC_POLL_SECONDS", "5"))
            if worker_poll_seconds < 0:
                queue_config.append("OCMEMOG_INGEST_ASYNC_POLL_SECONDS must be >= 0")
        except Exception:
            queue_config.append("OCMEMOG_INGEST_ASYNC_POLL_SECONDS")
        try:
            worker_batch_max = int(os.environ.get("OCMEMOG_INGEST_ASYNC_BATCH_MAX", "25"))
            if worker_batch_max < 1:
                queue_config.append("OCMEMOG_INGEST_ASYNC_BATCH_MAX must be >= 1")
        except Exception:
            queue_config.append("OCMEMOG_INGEST_ASYNC_BATCH_MAX")

        invalid = 0
        total = 0
        invalid_samples: list[dict[str, Any]] = []
        for raw_line in queue_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            total += 1
            try:
                json.loads(line)
            except Exception:
                invalid += 1
                if len(invalid_samples) < 3:
                    invalid_samples.append({"line_no": total, "line": line[:160]})

        status = "ok"
        messages: list[str] = []
        if invalid:
            status = "warn"
            messages.append(f"Queue has {invalid} invalid line(s).")
        if depth > 25:
            status = "warn"
            messages.append(f"Queue backlog is elevated ({depth}).")
        backlog_severity = _queue_backlog_severity(depth)
        if invalid or backlog_severity in {"medium", "high", "critical"}:
            status = "warn"
        if queue_config:
            status = "warn"
            messages.append("Queue config has invalid values: " + ", ".join(sorted(set(queue_config))))
        if depth > 0 and not worker_enabled and not queue_config:
            status = "warn"
            messages.append("Ingest worker is disabled but queue has pending entries.")
        if depth > 0 and worker_enabled and app._INGEST_WORKER_THREAD is not None and not app._INGEST_WORKER_THREAD.is_alive():
            status = "warn"
            messages.append("Ingest worker thread exists but is not currently alive.")
        hints: list[str] = []
        if invalid > 0:
            hints.append("Run --fix repair-queue to drop invalid queue entries.")
        if depth > 0 and not worker_enabled:
            hints.append("Enable OCMEMOG_INGEST_ASYNC_WORKER or flush with POST /memory/ingest_flush.")
        if depth > 1000:
            hints.append("Queue depth is very high; inspect upstream ingest failures and sidecar reachability.")
        worker_config_issues = queue_config
        if not worker_config_issues:
            if worker_batch_max and worker_batch_max > 40:
                hints.append("Ingest batch size is large; reduce OCMEMOG_INGEST_ASYNC_BATCH_MAX if queue consumers lag.")
        message = "; ".join(messages) if messages else "Queue state is healthy."
        if backlog_severity in {"medium", "high", "critical"} and "Queue state is healthy." in message:
            message = f"Queue backlog severity is {backlog_severity} ({depth})."
    except Exception as exc:
        return CheckResult(
            key="queue/health",
            label="queue health",
            status="fail",
            message=f"Queue health check failed: {exc}",
            details={"error": str(exc)},
            fixable=True,
            fix_action="repair-queue",
        )

    return CheckResult(
        key="queue/health",
        label="queue health",
        status=status,
        message=message,
        details={
            "queue_depth": depth,
            "queue_path": str(queue_path),
            "invalid_lines": invalid,
            "lines_seen": total,
            "stats": stats,
            "queue_bytes": queue_size,
            "queue_worker_enabled": worker_enabled,
            "queue_worker_poll_seconds": worker_poll_seconds,
            "queue_worker_batch_max": worker_batch_max,
            "queue_config_issues": queue_config,
            "invalid_payload_samples": invalid_samples,
            "ingest_worker_running": bool(app._INGEST_WORKER_THREAD and app._INGEST_WORKER_THREAD.is_alive()),
            "queue_backlog_severity": backlog_severity,
            "queue_hints": hints,
        },
        fixable=True,
        fix_action="repair-queue",
    )


def _run_transcript_watcher_sanity(_: None) -> CheckResult:
    try:
        app = _import_sidecar_app()
    except Exception as exc:
        return CheckResult(
            key="sidecar/transcript-watcher",
            label="sidecar transcript watcher",
            status="fail",
            message=f"Failed to import sidecar app for transcript watcher checks: {exc}",
            details={"error": str(exc)},
        )

    enabled, valid_toggle = app._parse_bool_env_value(os.environ.get("OCMEMOG_TRANSCRIPT_WATCHER"), default=False)
    issues: list[str] = []
    hints: list[str] = []
    config: dict[str, Any] = {
        "enabled": enabled,
        "watcher_thread_running": bool(app._WATCHER_THREAD and app._WATCHER_THREAD.is_alive()),
    }

    if not valid_toggle:
        config["watcher_toggle_parse_valid"] = False
        return CheckResult(
            key="sidecar/transcript-watcher",
            label="sidecar transcript watcher",
            status="warn",
            message="Transcript watcher env toggle is not valid boolean syntax.",
            details={"config": config, "issues": ["OCMEMOG_TRANSCRIPT_WATCHER must be a boolean value"], "hints": []},
        )

    if not enabled:
        return CheckResult(
            key="sidecar/transcript-watcher",
            label="sidecar transcript watcher",
            status="ok",
            message="Transcript watcher is disabled.",
            details={"enabled": False, "issues": [], "hints": [], "config": config},
        )
    if enabled:
        transcript_path = os.environ.get("OCMEMOG_TRANSCRIPT_PATH", "").strip()
        transcript_dir = os.environ.get("OCMEMOG_TRANSCRIPT_DIR", "").strip()
        session_dir = os.environ.get("OCMEMOG_SESSION_DIR", "").strip()
        config.update(
            {
                "transcript_path": transcript_path or None,
                "transcript_dir": transcript_dir or None,
                "session_dir": session_dir or None,
                "transcript_glob": os.environ.get("OCMEMOG_TRANSCRIPT_GLOB", "*.log"),
                "session_glob": os.environ.get("OCMEMOG_SESSION_GLOB", "*.jsonl"),
                "batch_seconds": os.environ.get("OCMEMOG_INGEST_BATCH_SECONDS", "30"),
                "batch_max": os.environ.get("OCMEMOG_INGEST_BATCH_MAX", "25"),
                "poll_seconds": os.environ.get("OCMEMOG_TRANSCRIPT_POLL_SECONDS", "30"),
                "start_at_end": os.environ.get("OCMEMOG_TRANSCRIPT_START_AT_END", "true"),
                "watcher_toggle_parse_valid": True,
            }
        )
        poll_seconds, issue = _parse_float_env("OCMEMOG_TRANSCRIPT_POLL_SECONDS", 30.0, minimum=1)
        if issue:
            issues.append(issue)
            hints.append("Set OCMEMOG_TRANSCRIPT_POLL_SECONDS to a positive number.")
        batch_seconds, issue = _parse_float_env("OCMEMOG_INGEST_BATCH_SECONDS", 30.0, minimum=1)
        if issue:
            issues.append(issue)
            hints.append("Set OCMEMOG_INGEST_BATCH_SECONDS to a positive number.")
        batch_max, issue = _parse_int_env("OCMEMOG_INGEST_BATCH_MAX", 25, minimum=1)
        if issue:
            issues.append(issue)
            hints.append("Set OCMEMOG_INGEST_BATCH_MAX to an integer >= 1.")
        reinforce_enabled, issue = _parse_bool_env("OCMEMOG_REINFORCE_SENTIMENT", True)
        if issue:
            issues.append(issue)
            hints.append("Set OCMEMOG_REINFORCE_SENTIMENT to true/false.")
        config.update(
            {
                "poll_seconds": poll_seconds,
                "batch_seconds": batch_seconds,
                "batch_max": batch_max,
                "reinforce_sentiment": reinforce_enabled,
            }
        )

        for raw_value in (transcript_path, transcript_dir, session_dir):
            if raw_value:
                target = Path(raw_value).expanduser().resolve()
                if not target.exists():
                    hints.append(f"Configured path '{target}' does not currently exist; watcher will create as needed.")
                elif target.is_file() and target.suffix == "":
                    issues.append(f"Configured path '{target}' looks like a directory but is a file path.")

        ingest_endpoint = os.environ.get("OCMEMOG_INGEST_ENDPOINT", "http://127.0.0.1:17891/memory/ingest_async")
        turn_ingest_endpoint = os.environ.get("OCMEMOG_TURN_INGEST_ENDPOINT", "")
        config["ingest_endpoint"] = ingest_endpoint
        config["turn_ingest_endpoint"] = turn_ingest_endpoint or ingest_endpoint.replace("/memory/ingest_async", "/conversation/ingest_turn")
        if not config["turn_ingest_endpoint"].startswith("http"):
            issues.append("OCMEMOG_TURN_INGEST_ENDPOINT must be an absolute HTTP(S) URL when overridden.")
        config["watcher_thread_running"] = bool(app._WATCHER_THREAD and app._WATCHER_THREAD.is_alive())

    status = "ok"
    message = "Transcript watcher config is healthy."
    if issues:
        status = "warn"
        message = "Transcript watcher config has issues."

    return CheckResult(
        key="sidecar/transcript-watcher",
        label="sidecar transcript watcher",
        status=status,
        message=message,
        details={
            "config": config,
            "issues": issues,
            "hints": hints,
            "enabled": enabled,
            "watcher_running": bool(app._WATCHER_THREAD and app._WATCHER_THREAD.is_alive()),
            "watcher_toggle_parse_valid": valid_toggle,
        },
    )


def _collect_vector_backlog() -> dict[str, Any]:
    try:
        store.init_db()
    except Exception:
        pass
    backlog: dict[str, int] = {}
    conn = store.connect()
    total_missing = 0
    query_errors: list[str] = []
    try:
        for table in store.MEMORY_TABLES:
            try:
                total = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
                indexed = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM vector_embeddings WHERE source_type = ?",
                        (table,),
                    ).fetchone()[0]
                    or 0
                )
                missing = max(total - indexed, 0)
                backlog[table] = missing
                total_missing += missing
            except Exception as exc:
                query_errors.append(f"{table}: {exc}")
                backlog[table] = 0
    finally:
        conn.close()
    return {
        "per_table": backlog,
        "total_missing": total_missing,
        "severity": _vector_backlog_severity(total_missing),
        "errors": query_errors,
    }


def _run_transcript_root_readability(_: None) -> CheckResult:
    try:
        app = _import_sidecar_app()
    except Exception as exc:
        return CheckResult(
            key="sidecar/transcript-roots",
            label="sidecar transcript roots",
            status="fail",
            message=f"Failed to import sidecar app for transcript-root checks: {exc}",
            details={"error": str(exc)},
        )

    raw_roots = os.environ.get("OCMEMOG_TRANSCRIPT_ROOTS")
    try:
        roots = app._allowed_transcript_roots()
        root_values = [str(path) for path in roots]
        missing: list[str] = []
        non_directories: list[str] = []
        inaccessible: list[str] = []
        readable_roots: list[str] = []
        for path in roots:
            if not path.exists():
                missing.append(str(path))
            elif not path.is_dir():
                non_directories.append(str(path))
            elif not os.access(str(path), os.R_OK | os.X_OK):
                inaccessible.append(str(path))
            else:
                readable_roots.append(str(path))
    except Exception as exc:
        return CheckResult(
            key="sidecar/transcript-roots",
            label="sidecar transcript roots",
            status="fail",
            message=f"Could not evaluate transcript roots: {exc}",
            details={"error": str(exc)},
        )

    issues = missing + non_directories + inaccessible
    status = "ok"
    message = "Transcript root paths are readable."
    if raw_roots is not None and not roots:
        status = "warn"
        message = "OCMEMOG_TRANSCRIPT_ROOTS is set but contains no usable entries."
    elif issues:
        status = "warn"
        message = "One or more transcript root paths are not usable."

    return CheckResult(
        key="sidecar/transcript-roots",
        label="sidecar transcript roots",
        status=status,
        message=message,
        details={
            "configured_via_env": raw_roots is not None,
            "roots": root_values,
            "readable_roots": readable_roots,
            "missing_roots": missing,
            "non_directories": non_directories,
            "inaccessible_roots": inaccessible,
        },
    )


def _run_sidecar_toggle_sanity(_: None) -> CheckResult:
    try:
        app = _import_sidecar_app()
    except Exception as exc:
        return CheckResult(
            key="sidecar/env-toggles",
            label="sidecar environment toggles",
            status="fail",
            message=f"Failed to import sidecar app for env toggle checks: {exc}",
            details={"error": str(exc)},
        )

    invalid: list[str] = []
    checks: dict[str, dict[str, Any]] = {}
    for key in _ENV_TOGGLE_KEYS:
        raw = os.environ.get(key)
        if raw is None:
            continue
        parsed, valid = app._parse_bool_env_value(raw, default=False)
        checks[key] = {
            "raw": str(raw),
            "parsed": parsed,
            "valid": valid,
        }
        if not valid:
            invalid.append(key)

    status = "ok"
    message = "Boolean env toggles are valid."
    if invalid:
        status = "warn"
        message = "Invalid boolean env toggle value(s): " + ", ".join(sorted(invalid))

    if not checks:
        message = "No explicitly configured boolean toggles were found."

    return CheckResult(
        key="sidecar/env-toggles",
        label="sidecar environment toggles",
        status=status,
        message=message,
        details={"toggles": checks, "invalid": invalid},
    )


def _run_sidecar_http_auth(_: None) -> CheckResult:
    endpoint = os.environ.get("OCMEMOG_ENDPOINT", "http://127.0.0.1:17891")
    token = os.environ.get("OCMEMOG_API_TOKEN")
    probes: list[dict[str, Any]] = []
    issues: list[str] = []
    hints: list[str] = []
    status = "ok"
    message = "Sidecar HTTP auth configuration is healthy."

    if token:
        unauth_status, unauth_payload, unauth_error = _probe_health_json(endpoint)
        probes.append({
            "label": "unauthenticated",
            "status": unauth_status,
            "error": unauth_error,
            "ok": bool(unauth_payload.get("ok")) if isinstance(unauth_payload, dict) else None,
        })
        if unauth_error:
            status = "warn"
            message = "Sidecar health endpoint is not currently reachable."
            issues.append(unauth_error)
        elif unauth_status == 200:
            status = "warn"
            issues.append("Token configured, but authenticated endpoints are accepting unauthenticated access.")
            hints.append("Verify OCMEMOG_API_TOKEN is exported in both sidecar and operator processes.")
        elif unauth_status != 401:
            status = "warn"
            issues.append(f"Expected 401 for unauthenticated access, got {unauth_status}.")

        token_ok: list[str] = []
        for label, headers in (
            ("x-token", {"x-ocmemog-token": token}),
            ("bearer", {"authorization": f"Bearer {token}"}),
        ):
            auth_status, auth_payload, auth_error = _probe_health_json(endpoint, headers=headers)
            probes.append(
                {
                    "label": label,
                    "status": auth_status,
                    "error": auth_error,
                    "ok": bool(auth_payload.get("ok")) if isinstance(auth_payload, dict) else None,
                }
            )
            if not auth_error and auth_status == 200:
                token_ok.append(label)

        if not token_ok:
            status = "warn"
            issues.append("Token-based authenticated health check failed.")
            hints.append("Verify the token on operator and sidecar match and the expected header is supported.")

        details = {
            "token_required": True,
            "token_probe_headers": token_ok,
            "probes": probes,
            "hints": hints,
        }
    else:
        health_status, health_payload, health_error = _probe_health_json(endpoint)
        probes.append({
            "label": "unauthenticated",
            "status": health_status,
            "error": health_error,
            "ok": bool(health_payload.get("ok")) if isinstance(health_payload, dict) else None,
        })
        if health_error:
            status = "warn"
            message = "Sidecar health endpoint is not currently reachable."
            issues.append(health_error)
        elif health_status != 200 or not isinstance(health_payload, dict) or not health_payload.get("ok", False):
            status = "warn"
            issues.append("Sidecar health endpoint returned a non-OK response.")
        details = {
            "token_required": False,
            "probes": probes,
            "hints": hints,
        }

    if not issues:
        if message == "Sidecar HTTP auth configuration is healthy." and status == "ok":
            details["token_required"] = bool(token)
    else:
        message = "; ".join(issues)

    details["endpoint"] = endpoint
    return CheckResult(
        key="sidecar/http-auth",
        label="sidecar HTTP auth",
        status=status,
        message=message,
        details=details,
    )


def _probe_health_json(endpoint: str, headers: dict[str, str] | None = None, *, timeout: float = _HTTP_TIMEOUT_SECONDS) -> tuple[int | None, dict[str, Any] | None, str | None]:
    request_headers = {
        "Accept": "application/json",
    }
    if headers:
        request_headers.update(headers)
    request = Request(f"{endpoint.rstrip('/')}/healthz", method="GET")
    for key, value in request_headers.items():
        request.add_header(key, value)
    try:
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            raw = response.read(256).decode("utf-8", errors="ignore")
            payload: dict[str, Any] | None = None
            if raw:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    return status, None, "non-dict JSON payload"
            return status, payload, None
    except HTTPError as exc:
        raw = ""
        try:
            raw = exc.read(256).decode("utf-8", errors="ignore")
        except Exception:
            raw = ""
        payload: dict[str, Any] | None = None
        if raw:
            try:
                loaded = json.loads(raw)
                payload = loaded if isinstance(loaded, dict) else None
            except Exception:
                payload = None
        return getattr(exc, "code", None), payload, None
    except Exception as exc:
        return None, None, str(exc)


def _fix_create_paths(_: None) -> FixResult:
    try:
        created = []
        for target in (state_store.root_dir(), state_store.data_dir(), state_store.memory_dir(), state_store.reports_dir()):
            target.mkdir(parents=True, exist_ok=True)
            created.append(str(target))
            probe = target / ".ocmemog_doctor_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        return FixResult(
            action="create-missing-paths",
            check_key="state/path-writable",
            message="Created required state directories and confirmed writable state.",
            changed=len(created),
            ok=True,
        )
    except Exception as exc:
        return FixResult(
            action="create-missing-paths",
            check_key="state/path-writable",
            message=f"Could not create state paths: {exc}",
            changed=0,
            ok=False,
        )


def _fix_repair_queue(_: None) -> FixResult:
    try:
        app = _import_sidecar_app()
        queue_path = app._queue_path()
        queue_lines = []
        dropped = 0
        for raw_line in queue_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                dropped += 1
                continue
            queue_lines.append(json.dumps(payload, ensure_ascii=False))

        with app.QUEUE_LOCK:
            app._write_queue_lines(queue_lines)
        return FixResult(
            action="repair-queue",
            check_key="queue/health",
            message=f"Removed {dropped} invalid queue entry(ies).",
            changed=dropped,
            ok=True,
        )
    except Exception as exc:
        return FixResult(
            action="repair-queue",
            check_key="queue/health",
            message=f"Queue repair failed: {exc}",
            changed=0,
            ok=False,
        )


def _run_sidecar_import(_: None) -> CheckResult:
    try:
        app = _import_sidecar_app()
    except Exception as exc:
        return CheckResult(
            key="sidecar/app-import",
            label="sidecar app import",
            status="fail",
            message=f"Failed to import sidecar app module: {exc}",
            details={"error": str(exc)},
        )

    if not hasattr(app, "app"):
        return CheckResult(
            key="sidecar/app-import",
            label="sidecar app import",
            status="fail",
            message="ocmemog.sidecar.app did not expose FastAPI app object.",
            details={"module": "ocmemog.sidecar.app"},
        )
    return CheckResult(
        key="sidecar/app-import",
        label="sidecar app import",
        status="ok",
        message="sidecar app module imports and exposes FastAPI app.",
        details={"module": "ocmemog.sidecar.app", "app_type": type(app.app).__name__},
    )


def _check_http(endpoint: str) -> str | None:
    try:
        status, payload, error = _probe_health_json(endpoint)
        if error:
            return error
        if not status or status >= 400:
            return f"health endpoint status {status}"
        if not payload:
            return "empty response"
        if not isinstance(payload, dict) or not payload.get("ok"):
            return "health endpoint returned non-ok payload"
    except Exception as exc:
        return str(exc)
    return None


def _run_runtime_probe(_: None) -> CheckResult:
    details: dict[str, Any] = {}
    status = "ok"
    messages: list[str] = []

    try:
        runtime_status = sidecar_compat.probe_runtime()
        details["runtime_mode"] = runtime_status.mode
        details["missing_deps"] = runtime_status.missing_deps
        details["warnings"] = runtime_status.warnings
        details["todo"] = runtime_status.todo
    except Exception as exc:
        status = "fail"
        messages.append(f"runtime/probe import failed: {exc}")
        details["runtime_error"] = str(exc)
        return CheckResult(
            key="vector/runtime-probe",
            label="vector/runtime probe",
            status="fail",
            message="Runtime probe failed.",
            details=details,
        )

    try:
        payload = health.get_memory_health()
        details["memory_health"] = payload
        vector_backlog = _collect_vector_backlog()
        details["vector_backlog"] = vector_backlog
        memory_integrity_ok = payload.get("integrity", {}).get("ok", payload.get("vector_index_integrity_status"))
        if not memory_integrity_ok:
            status = "fail"
            messages.append("memory health reported failed integrity.")
        if vector_backlog.get("errors"):
            status = max(status, "warn", key=lambda s: _STATUS_PRECEDENCE[s]) if isinstance(status, str) else "warn"
            messages.append("Vector backlog probe reported query warnings: " + "; ".join(vector_backlog["errors"][:3]))
        if vector_backlog["total_missing"] > 0:
            status = max(status, "warn", key=lambda s: _STATUS_PRECEDENCE[s]) if isinstance(status, str) else "warn"
            messages.append(
                f"Vector backlog is elevated ({vector_backlog['total_missing']} rows, severity={vector_backlog['severity']})."
            )
            details["vector_backlog_hint"] = "Run scripts/ocmemog-backfill-vectors.py to reduce missing vector debt."
    except Exception as exc:
        status = "fail"
        details["memory_health_error"] = str(exc)
        messages.append(f"memory health check failed: {exc}")

    if runtime_status.mode != "ready":
        status = max(status, "warn", key=lambda s: _STATUS_PRECEDENCE[s]) if isinstance(status, str) else "warn"
        messages.append(
            f"runtime mode is degraded ({len(runtime_status.missing_deps)} missing item(s): "
            f"{', '.join(runtime_status.missing_deps) or 'none'})."
        )

    try:
        if not embedding_engine.generate_embedding("ocmemog doctor probe"):
            status = "fail"
            messages.append("embedding probe returned no vector.")
    except Exception as exc:
        status = "fail"
        details["embedding_error"] = str(exc)
        messages.append(f"embedding probe failed: {exc}")

    endpoint = os.environ.get("OCMEMOG_ENDPOINT", "http://127.0.0.1:17891")
    sidecar_error = _check_http(endpoint)
    if sidecar_error:
        status = max(status, "warn", key=lambda s: _STATUS_PRECEDENCE[s]) if isinstance(status, str) else "warn"
        details["sidecar_http_error"] = sidecar_error
        messages.append("sidecar HTTP probe not currently available.")
    else:
        details["sidecar_http"] = "ok"

    if not messages:
        messages.append("runtime, vector, and sidecar probe checks look healthy.")

    return CheckResult(
        key="vector/runtime-probe",
        label="vector/runtime probe",
        status=status,
        message="; ".join(messages),
        details=details,
    )


def _status_rank(status: str) -> int:
    return _STATUS_PRECEDENCE.get(status, 0)


def _overall_status(results: Iterable[CheckResult]) -> str:
    max_status = "ok"
    for result in results:
        if _status_rank(result.status) > _status_rank(max_status):
            max_status = result.status
    return max_status


DOCTOR_CHECKS: tuple[DoctorCheck, ...] = (
    DoctorCheck(key="runtime/imports", label="runtime module imports", check=_run_imports),
    DoctorCheck(key="state/path-writable", label="state path writability", check=_run_state_paths, fix_key="create-missing-paths", fix=_fix_create_paths),
    DoctorCheck(key="sqlite/schema-access", label="sqlite schema access", check=_run_sqlite_schema),
    DoctorCheck(key="queue/health", label="queue health", check=_run_queue_health, fix_key="repair-queue", fix=_fix_repair_queue),
    DoctorCheck(key="sidecar/http-auth", label="sidecar HTTP auth", check=_run_sidecar_http_auth),
    DoctorCheck(key="sidecar/transcript-roots", label="sidecar transcript roots", check=_run_transcript_root_readability),
    DoctorCheck(key="sidecar/transcript-watcher", label="sidecar transcript watcher", check=_run_transcript_watcher_sanity),
    DoctorCheck(key="sidecar/env-toggles", label="sidecar environment toggles", check=_run_sidecar_toggle_sanity),
    DoctorCheck(key="sidecar/app-import", label="sidecar app import", check=_run_sidecar_import),
    DoctorCheck(key="vector/runtime-probe", label="vector/runtime probe", check=_run_runtime_probe),
)


_KNOWN_FIXES = {
    "create-missing-paths": "state/path-writable",
    "repair-queue": "queue/health",
}


def run_doctor_checks(*, fix_actions: list[str] | None = None, include_checks: set[str] | None = None, state_dir: str | None = None, strict: bool = False):
    selected = [check for check in DOCTOR_CHECKS if not include_checks or check.key in include_checks]
    fix_actions = _normalize_fixes(fix_actions)
    if any(item not in _KNOWN_FIXES for item in fix_actions):
        unknown = sorted(set(fix_actions) - set(_KNOWN_FIXES))
        raise ValueError(f"unknown --fix action(s): {', '.join(unknown)}")

    with _scoped_state_dir(state_dir):
        results: list[CheckResult] = []
        applied_fixes: list[FixResult] = []

        for check in selected:
            result = check.check(None)
            if result.status != "ok" and check.fix and check.fix_key in fix_actions:
                fix = check.fix(None)
                if fix.ok:
                    result = check.check(None)
                result = CheckResult(
                    key=result.key,
                    label=result.label,
                    status=result.status,
                    message=result.message,
                    details=result.details,
                    fixable=result.fixable,
                    fixed=True,
                    fix_action=check.fix_key,
                    fix_details=asdict(fix),
                )
                applied_fixes.append(fix)
            results.append(result)

    status = _overall_status(results)
    if strict and status == "warn":
        status = "fail"

    return {
        "status": status,
        "checks": [asdict(item) for item in results],
        "fixes": [asdict(item) for item in applied_fixes],
        "strict": strict,
    }


def _render_text(report: dict[str, Any]) -> None:
    print("ocmemog doctor")
    for check in report["checks"]:
        status = check["status"]
        print(f"{_queue_status_to_icon(status):<4} {check['key']}: {check['message']}")
        details = check.get("details") or {}
        if details:
            details_text = json.dumps(details, sort_keys=True)
            print(f"      details: {details_text}")
        if check.get("fix_action") and check.get("fixed"):
            fix_details = check.get("fix_details") or {}
            changed = fix_details.get("changed", 0)
            fix_message = fix_details.get("message", "fix applied")
            print(f"      fix: {fix_message} (changed={changed})")
    summary = {
        "ok": sum(1 for item in report["checks"] if item["status"] == "ok"),
        "warn": sum(1 for item in report["checks"] if item["status"] == "warn"),
        "fail": sum(1 for item in report["checks"] if item["status"] == "fail"),
        "applied_fixes": len(report["fixes"]),
    }
    status = report["status"]
    print(f"summary: {json.dumps(summary, sort_keys=True)}")
    print(f"overall: {status}")


def _render_json(report: dict[str, Any]) -> None:
    payload = {
        "ok": report["status"] == "ok",
        "status": report["status"],
        "checks": report["checks"],
        "fixes": report["fixes"],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ocmemog-doctor",
        description="Run operator-oriented health checks for ocmemog.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    parser.add_argument(
        "--fix",
        action="append",
        default=[],
        help="Apply explicit low-risk fix action(s): create-missing-paths, repair-queue",
    )
    parser.add_argument(
        "--state-dir",
        help="Use an explicit state directory for all checks.",
    )
    parser.add_argument(
        "--check",
        action="append",
        default=[],
        help="Run only selected check key(s) (repeatable or comma-separated).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warn results as failures to hard-gate release checks.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    checks = set(_normalize_fixes(args.check))
    report = run_doctor_checks(
        fix_actions=args.fix,
        include_checks=checks,
        state_dir=args.state_dir,
        strict=args.strict,
    )
    if args.json:
        _render_json(report)
    else:
        _render_text(report)
    if report["status"] == "fail":
        return 2
    if report["status"] == "warn":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
