#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DURATION=${1:-30}
CONCURRENCY=${2:-10}
OUT=${3:-/tmp/ocmemog-failover.json}

"${ROOT_DIR}/scripts/ocmemog-load-test.py" \
  --mode mixed --duration "${DURATION}" --concurrency "${CONCURRENCY}" > "${OUT}" &
PID=$!

sleep 5
launchctl kickstart -k gui/$UID/com.openclaw.ocmemog.sidecar

wait ${PID}
cat "${OUT}"
