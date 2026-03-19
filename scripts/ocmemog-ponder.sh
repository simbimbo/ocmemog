#!/usr/bin/env bash
set -euo pipefail

ENDPOINT="${OCMEMOG_ENDPOINT:-http://127.0.0.1:17891}"
TOKEN="${OCMEMOG_API_TOKEN:-}"
MAX_ITEMS="${OCMEMOG_PONDER_ITEMS:-5}"

ARGS=("-H" "content-type: application/json")
if [[ -n "${TOKEN}" ]]; then
  ARGS+=("-H" "x-ocmemog-token: ${TOKEN}")
fi

QUEUE_DEPTH=$(python3 - <<'PY'
import json, urllib.request, os
endpoint = os.environ.get("OCMEMOG_ENDPOINT", "http://127.0.0.1:17891")
req = urllib.request.Request(f"{endpoint}/memory/ingest_status")
with urllib.request.urlopen(req, timeout=10) as resp:
    data = json.loads(resp.read().decode("utf-8"))
print(data.get("queueDepth", 0))
PY
)

if [[ "${QUEUE_DEPTH}" != "0" ]]; then
  echo "ponder skipped: queueDepth=${QUEUE_DEPTH}" >/tmp/ocmemog-ponder.last.json
  exit 0
fi

curl -s "${ENDPOINT}/memory/ponder" \
  "${ARGS[@]}" \
  -d "{\"max_items\":${MAX_ITEMS}}" >/tmp/ocmemog-ponder.last.json
