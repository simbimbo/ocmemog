from __future__ import annotations

import os
import unittest
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from ocmemog.sidecar import app as sidecar_app


class SidecarRouteTests(unittest.TestCase):
    def _runtime_payload(self) -> SimpleNamespace:
        return SimpleNamespace(
            mode="ready",
            missing_deps=[],
            todo=[],
            warnings=[],
            identity={"engine": "ocmemog-native"},
            capabilities=[],
            runtime_summary={
                "mode": "ready",
                "embedding_provider": "local-openai",
                "using_hash_embeddings": False,
                "shim_surface_count": 0,
                "missing_dep_count": 0,
                "warning_count": 0,
            },
        )

    @contextmanager
    def _client(self, *, runtime_status: SimpleNamespace | None = None):
        status = runtime_status or self._runtime_payload()
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.dict(
                    os.environ,
                    {
                        "OCMEMOG_INGEST_ASYNC_WORKER": "false",
                        "OCMEMOG_TRANSCRIPT_WATCHER": "false",
                    },
                    clear=False,
                )
            )
            stack.enter_context(mock.patch("ocmemog.sidecar.app._start_ingest_worker"))
            stack.enter_context(mock.patch("ocmemog.sidecar.app._start_transcript_watcher"))
            stack.enter_context(mock.patch("ocmemog.sidecar.app.probe_runtime", return_value=status))
            with stack.enter_context(TestClient(sidecar_app.app)) as client:
                yield client

    def test_healthz_route_returns_ok_and_runtime_payload(self) -> None:
        with self._client() as client:
            response = client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["mode"], "ready")
        self.assertIn("identity", payload)
        self.assertIn("runtimeSummary", payload)
        self.assertEqual(payload["identity"]["engine"], "ocmemog-native")

    def test_healthz_route_marks_non_ready_runtime(self) -> None:
        degraded = SimpleNamespace(
            mode="degraded",
            missing_deps=[],
            todo=[],
            warnings=["Runtime is bridged through compatibility shims."],
            identity={"engine": "ocmemog-native"},
            capabilities=[],
            runtime_summary={
                "mode": "degraded",
                "embedding_provider": "local-simple",
                "using_hash_embeddings": True,
                "shim_surface_count": 1,
                "missing_dep_count": 0,
                "warning_count": 1,
            },
        )
        with self._client(runtime_status=degraded) as client:
            response = client.get("/healthz")

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["mode"], "degraded")

    def test_memory_search_route_flattens_results(self) -> None:
        with self._client() as client, mock.patch(
            "ocmemog.sidecar.app.retrieval.retrieve_for_queries",
            return_value={"knowledge": [{"memory_reference": "knowledge:12", "content": "relevant memory", "score": 0.9}]},
        ) as retrieve_for_queries, mock.patch(
            "ocmemog.sidecar.app.flatten_results",
            return_value=[{"reference": "knowledge:12", "score": 0.9, "content": "relevant memory", "memory_status": "active", "governance_summary": {"memory_status": "active", "needs_review": False}}],
        ) as flatten_results, mock.patch.object(
            sidecar_app.vector_index,
            "get_last_search_diagnostics",
            return_value={"scan_limit": 1200, "prefilter_limit": 250, "candidate_rows": 1, "result_count": 1},
        ), mock.patch.object(
            sidecar_app.retrieval,
            "get_last_retrieval_diagnostics",
            return_value={
                "suppressed_by_governance": {"superseded": 1, "duplicate": 2},
                "suppressed_by_governance_by_bucket": {"knowledge": {"superseded": 1, "duplicate": 2}},
            },
        ):
            response = client.post(
                "/memory/search",
                json={"query": "relevant", "limit": 2, "categories": ["knowledge"]},
            )

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["usedFallback"])
        self.assertEqual(payload["query"], "relevant")
        self.assertEqual(payload["results"], [{"reference": "knowledge:12", "score": 0.9, "content": "relevant memory", "memory_status": "active", "governance_summary": {"memory_status": "active", "needs_review": False}}])
        self.assertEqual(payload["searchDiagnostics"]["strategy"], "hybrid")
        self.assertEqual(payload["searchDiagnostics"]["bucket_counts"], {"knowledge": 1})
        self.assertEqual(payload["searchDiagnostics"]["result_count"], 1)
        self.assertEqual(payload["searchDiagnostics"]["requested_limit"], 2)
        self.assertIn("vector_search", payload["searchDiagnostics"])
        self.assertIn("scan_limit", payload["searchDiagnostics"]["vector_search"])
        self.assertIn("execution_path", payload["searchDiagnostics"])
        self.assertFalse(payload["searchDiagnostics"]["execution_path"]["route_exception_fallback"])
        self.assertIn("governance_rollup", payload["searchDiagnostics"])
        self.assertEqual(payload["searchDiagnostics"]["governance_rollup"]["status_counts"], {"active": 1})
        self.assertEqual(payload["searchDiagnostics"]["retrieval_governance"]["suppressed_by_governance"], {"superseded": 1, "duplicate": 2})
        self.assertEqual(payload["searchDiagnostics"]["retrieval_governance"]["suppressed_by_governance_by_bucket"], {"knowledge": {"superseded": 1, "duplicate": 2}})
        retrieve_for_queries.assert_called_once()
        flatten_results.assert_called_once()

    def test_memory_search_route_marks_exception_fallback_path(self) -> None:
        with self._client() as client, mock.patch(
            "ocmemog.sidecar.app.retrieval.retrieve_for_queries",
            side_effect=RuntimeError("boom"),
        ), mock.patch(
            "ocmemog.sidecar.app._fallback_search",
            return_value=[{"reference": "knowledge:77", "score": 0.2, "content": "fallback memory"}],
        ):
            response = client.post(
                "/memory/search",
                json={"query": "relevant", "limit": 2, "categories": ["knowledge"]},
            )

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["usedFallback"])
        self.assertTrue(payload["searchDiagnostics"]["execution_path"]["route_exception_fallback"])

    def test_auto_hydration_policy_route_reports_agent_decision(self) -> None:
        with self._client() as client, mock.patch.dict(
            os.environ,
            {
                "OCMEMOG_AUTO_HYDRATION": "true",
                "OCMEMOG_AUTO_HYDRATION_ALLOW_AGENT_IDS": "main,worker",
                "OCMEMOG_AUTO_HYDRATION_DENY_AGENT_IDS": "chat-local",
            },
            clear=False,
        ):
            response = client.post("/memory/auto_hydration/policy", json={"agent_id": "chat-local"})

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["policy"]["reason"], "denied_by_agent_id")
        self.assertFalse(payload["policy"]["allowed"])

    def test_memory_get_route_hydrates_reference(self) -> None:
        with self._client() as client, mock.patch(
            "ocmemog.sidecar.app.provenance.hydrate_reference",
            return_value={"reference": "knowledge:9", "content": "stored memory"},
        ):
            response = client.post("/memory/get", json={"reference": "knowledge:9"})

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["memory"]["content"], "stored memory")

    def test_memory_get_route_rejects_invalid_reference(self) -> None:
        with self._client() as client:
            response = client.post("/memory/get", json={"reference": "invalidref"})

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "invalid_reference")

    def test_memory_ingest_route_stores_memory_reference(self) -> None:
        with self._client() as client, mock.patch(
            "ocmemog.sidecar.app.api.store_memory",
            return_value=7,
        ) as store_memory:
            response = client.post(
                "/memory/ingest",
                json={"content": "I prefer concise logs", "kind": "memory", "memory_type": "knowledge"},
            )

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["kind"], "memory")
        self.assertEqual(payload["reference"], "knowledge:7")
        store_memory.assert_called_once()

    def test_conversation_hydrate_route_returns_state_summary(self) -> None:
        with self._client() as client, mock.patch(
            "ocmemog.sidecar.app.conversation_state.get_recent_turns",
            return_value=[{"id": 1, "role": "user", "content": "hello"}],
        ) as get_recent_turns, mock.patch(
            "ocmemog.sidecar.app.conversation_state.get_linked_memories",
            return_value=[{"reference": "knowledge:1"}],
        ), mock.patch(
            "ocmemog.sidecar.app.conversation_state.get_turn_counts",
            return_value={"total": 1},
        ), mock.patch(
            "ocmemog.sidecar.app.conversation_state.list_relevant_unresolved_state",
            return_value=[],
        ), mock.patch(
            "ocmemog.sidecar.app.conversation_state.get_latest_checkpoint",
            return_value={"id": 3},
        ) as get_latest_checkpoint, mock.patch(
            "ocmemog.sidecar.app.conversation_state.infer_hydration_payload",
            return_value={"checkpoint_graph": {"active": True}, "active_branch": "branch-1"},
        ) as infer_hydration_payload, mock.patch(
            "ocmemog.sidecar.app.conversation_state.refresh_state",
            return_value={"metadata": {"state_status": "fresh"}},
        ), mock.patch(
            "ocmemog.sidecar.app.memory_links.get_memory_links_for_thread",
            return_value=[],
        ), mock.patch(
            "ocmemog.sidecar.app.memory_links.get_memory_links_for_session",
            return_value=[],
        ), mock.patch(
            "ocmemog.sidecar.app.memory_links.get_memory_links_for_conversation",
            return_value=[],
        ):
            response = client.post(
                "/conversation/hydrate",
                json={"conversation_id": "conv-1", "thread_id": "thread-1", "session_id": "session-1"},
            )

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["active_branch"], "branch-1")
        self.assertEqual(payload["linked_memories"], [{"reference": "knowledge:1"}])
        get_recent_turns.assert_called_once()
        get_latest_checkpoint.assert_called_once()
        infer_hydration_payload.assert_called_once()

    def test_dashboard_route_renders_html(self) -> None:
        class _Cursor:
            def fetchone(self):
                return (1,)

            def close(self):
                return None

        class _Conn:
            def execute(self, *_args, **_kwargs):
                return _Cursor()

            def close(self):
                return None

        with self._client() as client, mock.patch(
            "ocmemog.sidecar.app.health.get_memory_health",
            return_value={
                "counts": {"knowledge": 3},
                "local_inference": {"cache_hits": 5, "local_warm_calls": 4, "local_cold_calls": 1, "local_success": 2, "local_errors": 0, "frontier_calls_avoided_est": 1, "prompt_tokens_saved_est": 0, "completion_tokens_saved_est": 0, "cost_saved_usd_est": 0},
                "vector_index_count": 3,
                "vector_index_coverage": 0.66,
            },
        ), mock.patch("ocmemog.sidecar.app.store.connect", return_value=_Conn()):
            response = client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn("<title>ocmemog realtime</title>", response.text)
        self.assertIn("Vector coverage", response.text)
        self.assertIn("knowledge", response.text)

    def test_auth_middleware_blocks_and_allows_requests(self) -> None:
        with mock.patch.object(sidecar_app, "API_TOKEN", "test-secret"):
            with self._client() as client:
                unauthorized = client.get("/healthz")
                authorized = client.get("/healthz", headers={"x-ocmemog-token": "test-secret"})
                bearer_authorized = client.get("/healthz", headers={"authorization": "Bearer test-secret"})

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(unauthorized.json(), {"ok": False, "error": "unauthorized"})
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(authorized.json()["mode"], "ready")
        self.assertEqual(bearer_authorized.status_code, 200)
        self.assertEqual(bearer_authorized.json()["mode"], "ready")

    def test_tail_events_logs_read_failures(self) -> None:
        fake_path = mock.Mock()
        fake_path.exists.return_value = True
        fake_path.read_text.side_effect = OSError("boom")
        with mock.patch("ocmemog.sidecar.app.state_store.report_log_path", return_value=fake_path):
            with mock.patch("sys.stderr") as stderr:
                result = sidecar_app._tail_events()
        self.assertEqual(result, "")
        written = "".join(call.args[0] for call in stderr.write.call_args_list if call.args)
        self.assertIn("tail_read_failed", written)


if __name__ == "__main__":
    unittest.main()
