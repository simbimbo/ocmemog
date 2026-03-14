from __future__ import annotations


class _ProviderExecuteShim:
    __shim__ = True

    def execute_embedding_call(self, _selection, _text: str) -> dict[str, object]:
        return {}


provider_execute = _ProviderExecuteShim()
