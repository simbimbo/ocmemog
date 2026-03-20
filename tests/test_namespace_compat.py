from __future__ import annotations

import importlib
import unittest


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

        self.assertEqual(state_store.__name__, "brain.runtime.state_store")
        self.assertEqual(providers.__name__, "brain.runtime.providers")
        self.assertEqual(embedding_engine.__name__, "brain.runtime.memory.embedding_engine")
        self.assertEqual(memory_links.__name__, "brain.runtime.memory.memory_links")
        self.assertEqual(store.__name__, "brain.runtime.memory.store")
        self.assertEqual(api.__name__, "brain.runtime.memory.api")
        self.assertEqual(retrieval.__name__, "brain.runtime.memory.retrieval")

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
