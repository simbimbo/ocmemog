from __future__ import annotations

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
            "sidecar/transcript-roots",
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


if __name__ == "__main__":
    unittest.main()
