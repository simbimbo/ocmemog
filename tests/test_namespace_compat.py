from __future__ import annotations

import importlib
import unittest


class NamespaceCompatTests(unittest.TestCase):
    def test_runtime_modules_share_legacy_backing_modules(self) -> None:
        native_state_store = importlib.import_module("ocmemog.runtime.state_store")
        legacy_state_store = importlib.import_module("brain.runtime.state_store")
        native_store = importlib.import_module("ocmemog.runtime.memory.store")
        legacy_store = importlib.import_module("brain.runtime.memory.store")

        self.assertIs(native_state_store, legacy_state_store)
        self.assertIs(native_store, legacy_store)

    def test_runtime_package_exports_aliases(self) -> None:
        from ocmemog.runtime import providers, state_store
        from ocmemog.runtime.memory import api, retrieval, store

        self.assertEqual(state_store.__name__, "brain.runtime.state_store")
        self.assertEqual(providers.__name__, "brain.runtime.providers")
        self.assertEqual(store.__name__, "brain.runtime.memory.store")
        self.assertEqual(api.__name__, "brain.runtime.memory.api")
        self.assertEqual(retrieval.__name__, "brain.runtime.memory.retrieval")


if __name__ == "__main__":
    unittest.main()
