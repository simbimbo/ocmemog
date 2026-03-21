from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ocmemog.sidecar import app


class SidecarBooleanEnvParsingTests(unittest.TestCase):
    def test_parse_bool_env_default_false(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False) as env:
            env.pop("OCMEMOG_TEST_BOOL", None)
            self.assertFalse(app._parse_bool_env("OCMEMOG_TEST_BOOL", default=False))

    def test_parse_bool_env_default_true(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False) as env:
            env.pop("OCMEMOG_TEST_BOOL", None)
            self.assertTrue(app._parse_bool_env("OCMEMOG_TEST_BOOL", default=True))

    def test_parse_bool_env_common_inputs(self) -> None:
        with mock.patch.dict(os.environ, {"OCMEMOG_TEST_BOOL": "1"}, clear=False):
            self.assertTrue(app._parse_bool_env("OCMEMOG_TEST_BOOL"))
        with mock.patch.dict(os.environ, {"OCMEMOG_TEST_BOOL": "TRUE"}, clear=False):
            self.assertTrue(app._parse_bool_env("OCMEMOG_TEST_BOOL"))
        with mock.patch.dict(os.environ, {"OCMEMOG_TEST_BOOL": "true"}, clear=False):
            self.assertTrue(app._parse_bool_env("OCMEMOG_TEST_BOOL"))
        with mock.patch.dict(os.environ, {"OCMEMOG_TEST_BOOL": "yes"}, clear=False):
            self.assertTrue(app._parse_bool_env("OCMEMOG_TEST_BOOL"))
        with mock.patch.dict(os.environ, {"OCMEMOG_TEST_BOOL": "on"}, clear=False):
            self.assertTrue(app._parse_bool_env("OCMEMOG_TEST_BOOL"))
        with mock.patch.dict(os.environ, {"OCMEMOG_TEST_BOOL": "y"}, clear=False):
            self.assertTrue(app._parse_bool_env("OCMEMOG_TEST_BOOL"))
        with mock.patch.dict(os.environ, {"OCMEMOG_TEST_BOOL": " t "}, clear=False):
            self.assertTrue(app._parse_bool_env("OCMEMOG_TEST_BOOL"))
        with mock.patch.dict(os.environ, {"OCMEMOG_TEST_BOOL": "0"}, clear=False):
            self.assertFalse(app._parse_bool_env("OCMEMOG_TEST_BOOL"))
        with mock.patch.dict(os.environ, {"OCMEMOG_TEST_BOOL": "false"}, clear=False):
            self.assertFalse(app._parse_bool_env("OCMEMOG_TEST_BOOL"))
        with mock.patch.dict(os.environ, {"OCMEMOG_TEST_BOOL": "no"}, clear=False):
            self.assertFalse(app._parse_bool_env("OCMEMOG_TEST_BOOL"))
        with mock.patch.dict(os.environ, {"OCMEMOG_TEST_BOOL": "off"}, clear=False):
            self.assertFalse(app._parse_bool_env("OCMEMOG_TEST_BOOL"))
        with mock.patch.dict(os.environ, {"OCMEMOG_TEST_BOOL": "N"}, clear=False):
            self.assertFalse(app._parse_bool_env("OCMEMOG_TEST_BOOL"))
        with mock.patch.dict(os.environ, {"OCMEMOG_TEST_BOOL": "F"}, clear=False):
            self.assertFalse(app._parse_bool_env("OCMEMOG_TEST_BOOL"))
        with mock.patch.dict(os.environ, {"OCMEMOG_TEST_BOOL": "nonsense"}, clear=False):
            self.assertFalse(app._parse_bool_env("OCMEMOG_TEST_BOOL"))


class SidecarShutdownQueueLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["OCMEMOG_STATE_DIR"] = self.tempdir.name
        app.QUEUE_STATS.update({
            "last_run": None,
            "processed": 0,
            "errors": 0,
            "last_error": None,
            "last_batch": 0,
        })
        app._INGEST_WORKER_THREAD = None
        app._WATCHER_THREAD = None
        app._INGEST_WORKER_STOP.clear()
        app._WATCHER_STOP.clear()

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("OCMEMOG_STATE_DIR", None)

    def test_shutdown_queue_drain_disabled_leaves_queue_intact(self) -> None:
        app._enqueue_payload({
            "content": "do not drain at shutdown",
            "kind": "memory",
            "memory_type": "knowledge",
        })

        with mock.patch("ocmemog.sidecar.app._ingest_request", return_value={"ok": True}) as ingest_request:
            with mock.patch.dict(os.environ, {"OCMEMOG_SHUTDOWN_DRAIN_QUEUE": "false", "OCMEMOG_STATE_DIR": self.tempdir.name}, clear=False):
                app._stop_background_workers()

        self.assertEqual(app._queue_depth(), 1)
        ingest_request.assert_not_called()

    def test_shutdown_queue_drain_enabled_consumes_remaining_queue(self) -> None:
        app._enqueue_payload({
            "content": "drain at shutdown",
            "kind": "memory",
            "memory_type": "knowledge",
        })

        with mock.patch("ocmemog.sidecar.app._ingest_request", return_value={"ok": True}) as ingest_request:
            with mock.patch.dict(os.environ, {"OCMEMOG_SHUTDOWN_DRAIN_QUEUE": "true", "OCMEMOG_STATE_DIR": self.tempdir.name}, clear=False):
                app._stop_background_workers()

        self.assertEqual(app._queue_depth(), 0)
        self.assertEqual(ingest_request.call_count, 1)

    def test_shutdown_queue_drain_accepts_mixed_case_true(self) -> None:
        app._enqueue_payload({
            "content": "drain at shutdown via mixed case env",
            "kind": "memory",
            "memory_type": "knowledge",
        })

        with mock.patch("ocmemog.sidecar.app._ingest_request", return_value={"ok": True}) as ingest_request:
            with mock.patch.dict(os.environ, {"OCMEMOG_SHUTDOWN_DRAIN_QUEUE": "TRUE", "OCMEMOG_STATE_DIR": self.tempdir.name}, clear=False):
                app._stop_background_workers()

        self.assertEqual(app._queue_depth(), 0)
        self.assertEqual(ingest_request.call_count, 1)

    def test_shutdown_timeout_invalid_or_negative_uses_default(self) -> None:
        for raw in ("not-a-number", "-5"):
            ingest_thread = mock.Mock()
            ingest_thread.is_alive.return_value = True
            watcher_thread = mock.Mock()
            watcher_thread.is_alive.return_value = True
            app._INGEST_WORKER_THREAD = ingest_thread
            app._WATCHER_THREAD = watcher_thread
            with mock.patch("ocmemog.sidecar.app._ingest_request", return_value={"ok": True}):
                with mock.patch.dict(os.environ, {
                    "OCMEMOG_SHUTDOWN_DRAIN_QUEUE": "false",
                    "OCMEMOG_WORKER_SHUTDOWN_TIMEOUT_SECONDS": raw,
                    "OCMEMOG_STATE_DIR": self.tempdir.name,
                }, clear=False):
                    app._stop_background_workers()
            ingest_thread.join.assert_called_once_with(timeout=0.35)
            watcher_thread.join.assert_called_once_with(timeout=0.35)
            app._INGEST_WORKER_STOP.clear()
            app._WATCHER_STOP.clear()


class SidecarIngestWorkerConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        app._INGEST_WORKER_STOP.clear()

    def tearDown(self) -> None:
        app._INGEST_WORKER_STOP.clear()

    def test_ingest_worker_rejects_invalid_config_and_uses_defaults(self) -> None:
        with mock.patch("ocmemog.sidecar.app._parse_bool_env", return_value=True):
            with mock.patch("ocmemog.sidecar.app._process_queue") as process_queue, \
                    mock.patch.object(app._INGEST_WORKER_STOP, "wait", return_value=True) as wait:
                with mock.patch.dict(os.environ, {
                    "OCMEMOG_INGEST_ASYNC_POLL_SECONDS": "not-a-number",
                    "OCMEMOG_INGEST_ASYNC_BATCH_MAX": "0",
                }, clear=False):
                    app._ingest_worker()

        process_queue.assert_called_once_with(25)
        wait.assert_called_once_with(5.0)


if __name__ == "__main__":
    unittest.main()
