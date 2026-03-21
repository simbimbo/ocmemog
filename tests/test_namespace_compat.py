from __future__ import annotations

import importlib
import unittest
from unittest.mock import patch


class NamespaceCompatTests(unittest.TestCase):
    def test_runtime_modules_share_legacy_backing_modules(self) -> None:
        native_state_store = importlib.import_module("ocmemog.runtime.state_store")
        legacy_state_store = importlib.import_module("brain.runtime.state_store")
        native_embedding_engine = importlib.import_module("ocmemog.runtime.memory.embedding_engine")
        legacy_embedding_engine = importlib.import_module("brain.runtime.memory.embedding_engine")
        native_memory_links = importlib.import_module("ocmemog.runtime.memory.memory_links")
        legacy_memory_links = importlib.import_module("brain.runtime.memory.memory_links")
        native_store = importlib.import_module("ocmemog.runtime.memory.store")
        legacy_store = importlib.import_module("brain.runtime.memory.store")

        self.assertIs(native_state_store, legacy_state_store)
        self.assertIs(native_embedding_engine, legacy_embedding_engine)
        self.assertIs(native_memory_links, legacy_memory_links)
        self.assertIs(native_store, legacy_store)

    def test_runtime_package_exports_aliases(self) -> None:
        from ocmemog.runtime import providers, state_store
        from ocmemog.runtime.memory import api, embedding_engine, memory_links, retrieval, store
        from ocmemog.runtime import identity, roles

        self.assertEqual(state_store.__name__, "brain.runtime.state_store")
        self.assertEqual(providers.__name__, "brain.runtime.providers")
        self.assertEqual(embedding_engine.__name__, "brain.runtime.memory.embedding_engine")
        self.assertEqual(memory_links.__name__, "brain.runtime.memory.memory_links")
        self.assertEqual(store.__name__, "brain.runtime.memory.store")
        self.assertEqual(api.__name__, "brain.runtime.memory.api")
        self.assertEqual(retrieval.__name__, "brain.runtime.memory.retrieval")
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

    def test_native_imports_expose_legacy_dependencies_inside_core_modules(self) -> None:
        native_api = importlib.import_module("ocmemog.runtime.memory.api")
        native_retrieval = importlib.import_module("ocmemog.runtime.memory.retrieval")
        native_vector_index = importlib.import_module("ocmemog.runtime.memory.vector_index")

        self.assertIs(native_api.provenance, importlib.import_module("brain.runtime.memory.provenance"))
        self.assertIs(native_api.store, importlib.import_module("brain.runtime.memory.store"))
        self.assertIs(native_retrieval.memory_links, importlib.import_module("brain.runtime.memory.memory_links"))
        self.assertIs(native_retrieval.vector_index, importlib.import_module("brain.runtime.memory.vector_index"))
        self.assertIs(native_vector_index.embedding_engine, importlib.import_module("brain.runtime.memory.embedding_engine"))
        self.assertIs(native_vector_index.redaction, importlib.import_module("brain.runtime.security.redaction"))


if __name__ == "__main__":
    unittest.main()
