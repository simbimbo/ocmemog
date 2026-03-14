from __future__ import annotations

import json
import os
import re
import urllib.request

from brain.runtime import config


def infer(prompt: str, provider_name: str | None = None) -> dict[str, str]:
    if not isinstance(prompt, str) or not prompt.strip():
        return {"status": "error", "error": "empty_prompt"}

    api_key = os.environ.get("OCMEMOG_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"status": "error", "error": "missing_api_key"}

    model = provider_name or config.OCMEMOG_MEMORY_MODEL
    url = f"{config.OCMEMOG_OPENAI_API_BASE.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"status": "error", "error": f"request_failed:{exc}"}

    try:
        output = response["choices"][0]["message"]["content"]
    except Exception:
        return {"status": "error", "error": "invalid_response"}

    return {"status": "ok", "output": str(output).strip()}


def parse_operator_name(text: str) -> dict[str, str] | None:
    match = re.search(r"\bmy name is ([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    return {"name": match.group(1)}
