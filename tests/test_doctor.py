from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ocmemog import doctor


class DoctorRegistryTests(unittest.TestCase):
    def test_expected_checks_are_registered(self) -> None:
        keys = {entry.key for entry in doctor.DOCTOR_CHECKS}
        for key in (
            "runtime/imports",
            "state/path-writable",
            "sqlite/schema-access",
            "queue/health",
            "sidecar/http-auth",
            "sidecar/transcript-roots",
            "sidecar/transcript-watcher",
            "sidecar/env-toggles",
            "sidecar/app-import",
            "vector/runtime-probe",
        ):
            self.assertIn(key, keys)


class DoctorQueueFixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OCMEMOG_STATE_DIR": self.tempdir.name}, clear=False)
        self.env.__enter__()

    def tearDown(self) -> None:
        self.env.__exit__(None, None, None)
        self.tempdir.cleanup()

    def test_queue_repair_fix_removes_invalid_entries(self) -> None:
        from ocmemog.sidecar import app

        queue_path = app._queue_path()
        queue_path.write_text('{"kind":"memory","content":"good"}\n{not-valid-json}\n', encoding="utf-8")

        pre_status = doctor.run_doctor_checks(
            include_checks={"queue/health"},
            fix_actions=[],
            state_dir=self.tempdir.name,
        )
        pre = next(check for check in pre_status["checks"] if check["key"] == "queue/health")
        self.assertEqual(pre["status"], "warn")
        self.assertEqual(pre["details"]["invalid_lines"], 1)

        post_status = doctor.run_doctor_checks(
            include_checks={"queue/health"},
            fix_actions=["repair-queue"],
            state_dir=self.tempdir.name,
        )
        post = next(check for check in post_status["checks"] if check["key"] == "queue/health")
        self.assertTrue(post["fixed"])
        self.assertEqual(post["status"], "ok")
        self.assertEqual(post_status["fixes"][0]["changed"], 1)

        lines = [line for line in queue_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)

    def test_queue_health_includes_severity_and_hints(self) -> None:
        from ocmemog.sidecar import app

        queue_path = app._queue_path()
        queue_path.write_text('{"kind":"memory","content":"ok"}\n' * 60, encoding="utf-8")

        report = doctor.run_doctor_checks(
            include_checks={"queue/health"},
            fix_actions=[],
            state_dir=self.tempdir.name,
        )
        check = next(item for item in report["checks"] if item["key"] == "queue/health")
        self.assertEqual(check["status"], "warn")
        self.assertIn(check["details"]["queue_backlog_severity"], ("medium", "high"))
        self.assertIn("queue_hints", check["details"])

    def test_queue_health_reports_retrying_payloads(self) -> None:
        from ocmemog.sidecar import app

        queue_path = app._queue_path()
        queue_path.write_text(
            json.dumps({"kind": "memory", "content": "retry me", "_ocmemog_retry_count": 2}) + "\n",
            encoding="utf-8",
        )

        report = doctor.run_doctor_checks(
            include_checks={"queue/health"},
            fix_actions=[],
            state_dir=self.tempdir.name,
        )
        check = next(item for item in report["checks"] if item["key"] == "queue/health")
        self.assertEqual(check["status"], "warn")
        self.assertEqual(check["details"]["retrying_lines"], 1)
        self.assertEqual(check["details"]["max_retry_seen"], 2)
        self.assertEqual(len(check["details"]["retrying_payload_samples"]), 1)
        self.assertIn("retrying payload", check["message"].lower())


class DoctorInvocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OCMEMOG_STATE_DIR": self.tempdir.name}, clear=False)
        self.env.__enter__()

    def tearDown(self) -> None:
        self.env.__exit__(None, None, None)
        self.tempdir.cleanup()

    def test_json_output_shape_from_doctor_checks(self) -> None:
        report = doctor.run_doctor_checks(
            include_checks={"runtime/imports", "state/path-writable", "sidecar/app-import"},
            state_dir=self.tempdir.name,
        )
        self.assertIn("status", report)
        self.assertIsInstance(report["checks"], list)
        self.assertIsInstance(report["fixes"], list)
        keys = {item["key"] for item in report["checks"]}
        self.assertSetEqual(keys, {"runtime/imports", "state/path-writable", "sidecar/app-import"})

    def test_unknown_fix_action_errors(self) -> None:
        with self.assertRaises(ValueError):
            doctor.run_doctor_checks(
                include_checks={"runtime/imports"},
                fix_actions=["not-a-fix"],
                state_dir=self.tempdir.name,
            )

    def test_unknown_check_key_errors(self) -> None:
        with self.assertRaises(ValueError):
            doctor.run_doctor_checks(
                include_checks={"not-a-check"},
                state_dir=self.tempdir.name,
            )


class DoctorRootAndToggleChecksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OCMEMOG_STATE_DIR": self.tempdir.name}, clear=False)
        self.env.__enter__()

    def tearDown(self) -> None:
        self.env.__exit__(None, None, None)
        self.tempdir.cleanup()

    def test_transcript_root_readability_warns_on_invalid_entries(self) -> None:
        root_dir = Path(self.tempdir.name)
        missing_root = root_dir / "missing"
        file_root = root_dir / "not-a-directory.txt"
        file_root.write_text("x", encoding="utf-8")

        with mock.patch.dict(os.environ, {"OCMEMOG_TRANSCRIPT_ROOTS": f"{missing_root},{file_root}"}, clear=False):
            report = doctor.run_doctor_checks(
                include_checks={"sidecar/transcript-roots"},
                state_dir=self.tempdir.name,
            )

        check = next(item for item in report["checks"] if item["key"] == "sidecar/transcript-roots")
        self.assertEqual(check["status"], "warn")
        self.assertIn(str(missing_root.resolve()), check["details"]["missing_roots"])
        self.assertIn(str(file_root.resolve()), check["details"]["non_directories"])

    def test_sidecar_toggle_sanity_reports_invalid_boolean(self) -> None:
        with mock.patch.dict(os.environ, {"OCMEMOG_TRANSCRIPT_WATCHER": "maybe"}, clear=False):
            report = doctor.run_doctor_checks(
                include_checks={"sidecar/env-toggles"},
                state_dir=self.tempdir.name,
            )

        check = next(item for item in report["checks"] if item["key"] == "sidecar/env-toggles")
        self.assertEqual(check["status"], "warn")
        self.assertIn("OCMEMOG_TRANSCRIPT_WATCHER", check["details"]["invalid"])

    def test_sidecar_toggle_sanity_accepts_boolean_synonyms(self) -> None:
        with mock.patch.dict(os.environ, {"OCMEMOG_SHUTDOWN_DRAIN_QUEUE": "TRUE", "OCMEMOG_USE_OLLAMA": "No"}, clear=False):
            report = doctor.run_doctor_checks(
                include_checks={"sidecar/env-toggles"},
                state_dir=self.tempdir.name,
            )

        check = next(item for item in report["checks"] if item["key"] == "sidecar/env-toggles")
        self.assertEqual(check["status"], "ok")
        self.assertEqual(check["details"]["toggles"]["OCMEMOG_SHUTDOWN_DRAIN_QUEUE"]["parsed"], True)
        self.assertEqual(check["details"]["toggles"]["OCMEMOG_USE_OLLAMA"]["parsed"], False)
        self.assertEqual(check["details"]["invalid"], [])


class DoctorSchemaAndWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OCMEMOG_STATE_DIR": self.tempdir.name}, clear=False)
        self.env.__enter__()

    def tearDown(self) -> None:
        self.env.__exit__(None, None, None)
        self.tempdir.cleanup()

    def test_sqlite_schema_check_reports_unexpected_schema_versions(self) -> None:
        from ocmemog.runtime import state_store
        import sqlite3

        report_init = doctor.run_doctor_checks(
            include_checks={"sqlite/schema-access"},
            state_dir=self.tempdir.name,
        )
        self.assertEqual(report_init["status"], "ok")

        conn = sqlite3.connect(str(state_store.memory_db_path()))
        try:
            conn.execute(
                "INSERT INTO knowledge (source, content, schema_version) VALUES (?, ?, ?)",
                ("unit-test", "legacy-row", "legacy-v0"),
            )
            conn.commit()
        finally:
            conn.close()

        report = doctor.run_doctor_checks(
            include_checks={"sqlite/schema-access"},
            state_dir=self.tempdir.name,
        )
        check = next(item for item in report["checks"] if item["key"] == "sqlite/schema-access")
        self.assertEqual(check["status"], "warn")
        versions = check["details"]["schema_versions"]["knowledge"]
        self.assertIn("legacy-v0", versions)

    def test_sidecar_http_auth_check_without_token(self) -> None:
        with mock.patch("ocmemog.doctor._probe_health_json", return_value=(200, {"ok": True}, None)):
            report = doctor.run_doctor_checks(
                include_checks={"sidecar/http-auth"},
                state_dir=self.tempdir.name,
            )

        check = next(item for item in report["checks"] if item["key"] == "sidecar/http-auth")
        self.assertEqual(check["status"], "ok")
        self.assertFalse(check["details"]["token_required"])

    def test_sidecar_http_auth_check_with_token(self) -> None:
        with mock.patch.dict(os.environ, {"OCMEMOG_API_TOKEN": "op-secret"}, clear=False), \
            mock.patch("ocmemog.doctor._probe_health_json", side_effect=[
                (401, {"ok": False, "error": "unauthorized"}, None),
                (200, {"ok": True}, None),
                (200, {"ok": True}, None),
            ]):
            report = doctor.run_doctor_checks(
                include_checks={"sidecar/http-auth"},
                state_dir=self.tempdir.name,
            )

        check = next(item for item in report["checks"] if item["key"] == "sidecar/http-auth")
        self.assertEqual(check["status"], "ok")
        self.assertTrue(check["details"]["token_required"])
        self.assertIn("x-token", check["details"]["token_probe_headers"])

    def test_sidecar_transcript_watcher_check_warns_invalid_config(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "OCMEMOG_TRANSCRIPT_WATCHER": "true",
                "OCMEMOG_TRANSCRIPT_POLL_SECONDS": "0",
                "OCMEMOG_INGEST_BATCH_SECONDS": "-1",
                "OCMEMOG_INGEST_BATCH_MAX": "0",
            },
            clear=False,
        ):
            report = doctor.run_doctor_checks(
                include_checks={"sidecar/transcript-watcher"},
                state_dir=self.tempdir.name,
            )

        check = next(item for item in report["checks"] if item["key"] == "sidecar/transcript-watcher")
        self.assertEqual(check["status"], "warn")
        self.assertTrue(check["details"]["enabled"])
        self.assertTrue(check["details"]["issues"])
class DoctorRuntimeProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"OCMEMOG_STATE_DIR": self.tempdir.name}, clear=False)
        self.env.__enter__()

    def tearDown(self) -> None:
        self.env.__exit__(None, None, None)
        self.tempdir.cleanup()

    def test_runtime_probe_accepts_memory_health_integrity_ok(self) -> None:
        runtime_status = SimpleNamespace(mode="ready", missing_deps=[], todo=[], warnings=[])

        with mock.patch("ocmemog.doctor.sidecar_compat.probe_runtime", return_value=runtime_status), \
            mock.patch("ocmemog.doctor.health.get_memory_health", return_value={"integrity": {"ok": True}}), \
            mock.patch("ocmemog.doctor.embedding_engine.generate_embedding", return_value="vector"), \
            mock.patch("ocmemog.doctor._check_http", return_value=None):
            report = doctor.run_doctor_checks(
                include_checks={"vector/runtime-probe"},
                state_dir=self.tempdir.name,
            )

        check = next(item for item in report["checks"] if item["key"] == "vector/runtime-probe")
        self.assertEqual(check["status"], "ok")
        self.assertEqual(check["details"]["runtime_mode"], "ready")
        self.assertEqual(check["details"]["sidecar_http"], "ok")
        self.assertNotIn("memory health reported failed integrity.", check["message"])

    def test_runtime_probe_reports_degraded_runtime_and_vector_backlog(self) -> None:
        runtime_status = SimpleNamespace(mode="degraded", missing_deps=["mock-missing"], todo=[], warnings=[])

        with mock.patch("ocmemog.doctor.sidecar_compat.probe_runtime", return_value=runtime_status), \
            mock.patch("ocmemog.doctor.health.get_memory_health", return_value={"integrity": {"ok": True}}), \
            mock.patch("ocmemog.doctor.embedding_engine.generate_embedding", return_value="vector"), \
            mock.patch("ocmemog.doctor._check_http", return_value=None), \
            mock.patch("ocmemog.doctor._collect_vector_backlog", return_value={"per_table": {"knowledge": 25}, "total_missing": 25, "severity": "low"}):
            report = doctor.run_doctor_checks(
                include_checks={"vector/runtime-probe"},
                state_dir=self.tempdir.name,
            )

        check = next(item for item in report["checks"] if item["key"] == "vector/runtime-probe")
        self.assertEqual(check["status"], "warn")
        self.assertEqual(check["details"]["runtime_mode"], "degraded")
        self.assertEqual(check["details"]["vector_backlog"]["total_missing"], 25)
        self.assertIn("runtime mode is degraded", check["message"])


if __name__ == "__main__":
    unittest.main()
