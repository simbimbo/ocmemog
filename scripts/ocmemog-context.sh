#!/usr/bin/env bash
set -euo pipefail

REF="${1:-}"
RADIUS="${2:-10}"
ENDPOINT="${OCMEMOG_ENDPOINT:-http://127.0.0.1:17890}"

if [[ -z "${REF}" ]]; then
  echo "usage: ocmemog-context.sh <reference> [radius]" >&2
  exit 1
fi

curl -s "${ENDPOINT}/memory/context" \
  -H 'content-type: application/json' \
  -d "{\"reference\":\"${REF}\",\"radius\":${RADIUS}}"
