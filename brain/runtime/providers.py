from __future__ import annotations

import json
import os
import urllib.request

from brain.runtime import config


class ProviderExecute:
    def execute_embedding_call(self, selection, text: str) -> dict[str, object]:
        provider_id = getattr(selection, "provider_id", "") or ""
        model = getattr(selection, "model", "") or config.OCMEMOG_OPENAI_EMBED_MODEL
        if provider_id != "openai":
            return {}
        api_key = os.environ.get("OCMEMOG_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return {}
        url = f"{config.OCMEMOG_OPENAI_API_BASE.rstrip('/')}/embeddings"
        payload = json.dumps({"model": model, "input": text}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return {}
        try:
            embedding = data["data"][0]["embedding"]
        except Exception:
            return {}
        return {"embedding": embedding}


provider_execute = ProviderExecute()
