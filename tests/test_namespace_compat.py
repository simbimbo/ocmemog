from __future__ import annotations

import importlib
import unittest
from unittest.mock import patch


class NamespaceCompatTests(unittest.TestCase):
    def test_runtime_modules_are_native_wrapped_modules(self) -> None:
        native_state_store = importlib.import_module("ocmemog.runtime.state_store")
        native_config = importlib.import_module("ocmemog.runtime.config")
        native_inference = importlib.import_module("ocmemog.runtime.inference")
        native_model_router = importlib.import_module("ocmemog.runtime.model_router")
        native_providers = importlib.import_module("ocmemog.runtime.providers")
        native_memory_links = importlib.import_module("ocmemog.runtime.memory.memory_links")
        native_memory_provenance = importlib.import_module("ocmemog.runtime.memory.provenance")
        native_memory_retrieval = importlib.import_module("ocmemog.runtime.memory.retrieval")
        native_memory_api = importlib.import_module("ocmemog.runtime.memory.api")
        native_memory_vector = importlib.import_module("ocmemog.runtime.memory.vector_index")
        native_memory_store = importlib.import_module("ocmemog.runtime.memory.store")
        native_memory_candidate = importlib.import_module("ocmemog.runtime.memory.candidate")
        native_memory_distill = importlib.import_module("ocmemog.runtime.memory.distill")
        native_memory_health = importlib.import_module("ocmemog.runtime.memory.health")
        native_memory_integrity = importlib.import_module("ocmemog.runtime.memory.integrity")
        native_memory_promote = importlib.import_module("ocmemog.runtime.memory.promote")
        native_embedding_engine = importlib.import_module("ocmemog.runtime.memory.embedding_engine")
        native_memory_conversation_state = importlib.import_module("ocmemog.runtime.memory.conversation_state")
        native_memory_pondering = importlib.import_module("ocmemog.runtime.memory.pondering_engine")
        native_memory_reinforcement = importlib.import_module("ocmemog.runtime.memory.reinforcement")
        native_memory_semantic = importlib.import_module("ocmemog.runtime.memory.semantic_search")
        native_memory_synthesis = importlib.import_module("ocmemog.runtime.memory.memory_synthesis")
        native_memory_salience = importlib.import_module("ocmemog.runtime.memory.memory_salience")
        native_freshness = importlib.import_module("ocmemog.runtime.memory.freshness")

        legacy_state_store = importlib.import_module("brain.runtime.state_store")
        legacy_config = importlib.import_module("brain.runtime.config")

        self.assertEqual(native_state_store.__name__, "ocmemog.runtime.state_store")
        self.assertEqual(native_config.__name__, "ocmemog.runtime.config")
        self.assertEqual(native_inference.__name__, "ocmemog.runtime.inference")
        self.assertEqual(native_model_router.__name__, "ocmemog.runtime.model_router")
        self.assertEqual(native_providers.__name__, "ocmemog.runtime.providers")
        self.assertEqual(native_memory_api.__name__, "ocmemog.runtime.memory.api")
        self.assertEqual(native_memory_vector.__name__, "ocmemog.runtime.memory.vector_index")
        self.assertEqual(native_memory_store.__name__, "ocmemog.runtime.memory.store")

        self.assertNotEqual(native_state_store, legacy_state_store)
        self.assertNotEqual(native_config, legacy_config)

        for module in (native_state_store, native_config, native_inference, native_model_router, native_providers):
            self.assertTrue(hasattr(module, "__wrapped_from__"))
            self.assertIn("brain.runtime", module.__wrapped_from__)
        for module in (
            native_memory_links,
            native_memory_provenance,
            native_memory_retrieval,
            native_memory_api,
            native_memory_candidate,
            native_memory_distill,
            native_memory_health,
            native_memory_integrity,
            native_memory_promote,
            native_memory_vector,
            native_memory_store,
            native_embedding_engine,
        ):
            self.assertFalse(hasattr(module, "__wrapped_from__"))
            self.assertFalse(module.__name__.startswith("brain.runtime."))
        for module in (
            native_memory_conversation_state,
            native_memory_pondering,
            native_memory_reinforcement,
            native_memory_semantic,
            native_memory_synthesis,
            native_memory_salience,
            native_freshness,
        ):
            self.assertEqual(module.__name__.split(".")[0], "ocmemog")
            self.assertFalse(module.__name__.startswith("brain.runtime."))
            self.assertFalse(hasattr(module, "__wrapped_from__"))

    def test_runtime_package_exports_aliases(self) -> None:
        from ocmemog.runtime import providers, state_store
        from ocmemog.runtime.memory import api, candidate, distill, health, integrity, promote, retrieval, store
        from ocmemog.runtime import identity, roles

        self.assertEqual(state_store.__name__, "ocmemog.runtime.state_store")
        self.assertEqual(providers.__name__, "ocmemog.runtime.providers")
        self.assertEqual(store.__name__, "ocmemog.runtime.memory.store")
        self.assertEqual(api.__name__, "ocmemog.runtime.memory.api")
        self.assertEqual(retrieval.__name__, "ocmemog.runtime.memory.retrieval")
        self.assertEqual(candidate.__name__, "ocmemog.runtime.memory.candidate")
        self.assertEqual(distill.__name__, "ocmemog.runtime.memory.distill")
        self.assertEqual(promote.__name__, "ocmemog.runtime.memory.promote")
        self.assertEqual(integrity.__name__, "ocmemog.runtime.memory.integrity")
        self.assertEqual(health.__name__, "ocmemog.runtime.memory.health")
        self.assertEqual(identity.__name__, "ocmemog.runtime.identity")
        self.assertEqual(roles.__name__, "ocmemog.runtime.roles")

    def test_runtime_identity_reports_capability_ownership(self) -> None:
        from ocmemog.sidecar.compat import probe_runtime
        status = probe_runtime()

        self.assertIsInstance(status.identity, dict)
        self.assertIsInstance(status.capabilities, list)
        self.assertGreaterEqual(len(status.capabilities), 1)
        caps_by_surface = {cap.get("surface"): cap for cap in status.capabilities}
        self.assertIn("ocmemog.runtime.roles", caps_by_surface)
        self.assertEqual(caps_by_surface["ocmemog.runtime.roles"].get("owner"), "ocmemog-native")
        self.assertEqual(status.identity.get("engine"), "ocmemog-native")

    def test_runtime_probe_keeps_startup_warning_clean_for_local_openai_provider(self) -> None:
        from ocmemog.sidecar.compat import probe_runtime

        with patch.dict("os.environ", {"BRAIN_EMBED_MODEL_PROVIDER": "local-openai"}, clear=False):
            with patch("ocmemog.sidecar.compat.importlib.util.find_spec", return_value=None):
                status = probe_runtime()

        self.assertFalse(any("sentence-transformers" in warning for warning in status.warnings))

    def test_runtime_probe_reports_native_ownership_for_key_surfaces(self) -> None:
        from ocmemog.sidecar.compat import probe_runtime
        from ocmemog.runtime.identity import SURFACE_COMPAT_OWNER, SURFACE_ENGINE_OWNER

        status = probe_runtime()
        caps_by_surface = {cap.get("surface"): cap for cap in status.capabilities}
        native_expected = {
            "ocmemog.runtime.storage_paths": SURFACE_ENGINE_OWNER,
            "ocmemog.runtime.roles": SURFACE_ENGINE_OWNER,
            "ocmemog.runtime.identity": SURFACE_ENGINE_OWNER,
        }
        compat_expected = {
            "ocmemog.runtime.config": SURFACE_COMPAT_OWNER,
            "ocmemog.runtime.inference": SURFACE_COMPAT_OWNER,
            "ocmemog.runtime.model_router": SURFACE_COMPAT_OWNER,
            "ocmemog.runtime.model_roles": SURFACE_COMPAT_OWNER,
            "ocmemog.runtime.providers": SURFACE_COMPAT_OWNER,
            "ocmemog.runtime.state_store": SURFACE_COMPAT_OWNER,
        }
        self.assertTrue(all(surface in caps_by_surface for surface in native_expected))
        self.assertTrue(all(surface in caps_by_surface for surface in compat_expected))
        for surface, expected_owner in native_expected.items():
            self.assertEqual(caps_by_surface[surface]["owner"], expected_owner)
            self.assertEqual(caps_by_surface[surface]["provider_module"], surface)
        for surface, expected_owner in compat_expected.items():
            self.assertEqual(caps_by_surface[surface]["owner"], expected_owner)
            legacy_surface = surface.replace("ocmemog.", "brain.", 1)
            self.assertEqual(caps_by_surface[surface]["provider_module"], legacy_surface)

        self.assertTrue(any(w.startswith("Runtime is bridged through") for w in status.warnings))

    def test_native_imports_expose_functional_contracts(self) -> None:
        native_api = importlib.import_module("ocmemog.runtime.memory.api")
        native_retrieval = importlib.import_module("ocmemog.runtime.memory.retrieval")
        native_vector_index = importlib.import_module("ocmemog.runtime.memory.vector_index")
        native_security_redaction = importlib.import_module("ocmemog.runtime.security.redaction")

        self.assertEqual(native_api.__name__, "ocmemog.runtime.memory.api")
        self.assertEqual(native_retrieval.__name__, "ocmemog.runtime.memory.retrieval")
        self.assertEqual(native_vector_index.__name__, "ocmemog.runtime.memory.vector_index")

        self.assertTrue(hasattr(native_api.provenance, "fetch_reference"))
        self.assertTrue(hasattr(native_api.store, "submit_write"))
        self.assertTrue(hasattr(native_api.store, "connect"))
        self.assertTrue(hasattr(native_retrieval, "retrieve"))
        self.assertTrue(hasattr(native_retrieval.memory_links, "get_memory_links"))
        self.assertTrue(hasattr(native_retrieval.vector_index, "search_memory"))
        self.assertTrue(hasattr(native_vector_index.embedding_engine, "generate_embedding"))
        self.assertTrue(hasattr(native_vector_index.redaction, "redact_text"))
        self.assertEqual(native_vector_index.redaction, native_security_redaction)

        redacted, flagged = native_vector_index.redaction.redact_text("email test@example.com +1 555-123-4567")
        self.assertIn("[redacted-email]", redacted)
        self.assertIn("[redacted-phone]", redacted)
        self.assertTrue(flagged)


if __name__ == "__main__":
    unittest.main()
