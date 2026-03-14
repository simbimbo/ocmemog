#!/usr/bin/env bash
set -euo pipefail

ENDPOINT="${OCMEMOG_ENDPOINT:-http://127.0.0.1:17890}"
TOKEN="${OCMEMOG_API_TOKEN:-}"
MAX_ITEMS="${OCMEMOG_PONDER_ITEMS:-5}"

ARGS=("-H" "content-type: application/json")
if [[ -n "${TOKEN}" ]]; then
  ARGS+=("-H" "x-ocmemog-token: ${TOKEN}")
fi

curl -s "${ENDPOINT}/memory/ponder" \
  "${ARGS[@]}" \
  -d "{\"max_items\":${MAX_ITEMS}}" >/tmp/ocmemog-ponder.last.json
