#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${OCMEMOG_HOST:-127.0.0.1}"
PORT="${OCMEMOG_PORT:-17890}"

export OCMEMOG_STATE_DIR="${OCMEMOG_STATE_DIR:-${ROOT_DIR}/.ocmemog-state}"
export PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

exec python3 -m uvicorn ocmemog.sidecar.app:app --host "${HOST}" --port "${PORT}"
