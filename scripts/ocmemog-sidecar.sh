#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${OCMEMOG_HOST:-127.0.0.1}"
PORT="${OCMEMOG_PORT:-17890}"
PYTHON_BIN="${OCMEMOG_PYTHON_BIN:-}"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
cd "${ROOT_DIR}"

export OCMEMOG_STATE_DIR="${OCMEMOG_STATE_DIR:-${ROOT_DIR}/.ocmemog-state}"
export PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
mkdir -p "${OCMEMOG_STATE_DIR}" "${OCMEMOG_STATE_DIR}/logs"

is_on_battery() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    return 1
  fi
  command -v pmset >/dev/null 2>&1 || return 1
  pmset -g batt 2>/dev/null | grep -q "Battery Power"
}

LAPTOP_MODE="${OCMEMOG_LAPTOP_MODE:-auto}"
if [[ "$LAPTOP_MODE" == "auto" ]]; then
  if is_on_battery; then
    LAPTOP_MODE="battery"
  else
    LAPTOP_MODE="ac"
  fi
fi
export OCMEMOG_LAPTOP_MODE="$LAPTOP_MODE"

# defaults for local ollama-backed inference/embeddings
export OCMEMOG_USE_OLLAMA="${OCMEMOG_USE_OLLAMA:-true}"
export OCMEMOG_OLLAMA_MODEL="${OCMEMOG_OLLAMA_MODEL:-phi3:latest}"
export OCMEMOG_OLLAMA_EMBED_MODEL="${OCMEMOG_OLLAMA_EMBED_MODEL:-nomic-embed-text:latest}"
export BRAIN_EMBED_MODEL_PROVIDER="${BRAIN_EMBED_MODEL_PROVIDER:-ollama}"
export BRAIN_EMBED_MODEL_LOCAL="${BRAIN_EMBED_MODEL_LOCAL:-}"

# battery-aware transcript watcher defaults
export OCMEMOG_TRANSCRIPT_WATCHER="${OCMEMOG_TRANSCRIPT_WATCHER:-true}"
export OCMEMOG_SESSION_DIR="${OCMEMOG_SESSION_DIR:-$HOME/.openclaw/agents/main/sessions}"
if [[ "$LAPTOP_MODE" == "battery" ]]; then
  export OCMEMOG_TRANSCRIPT_POLL_SECONDS="${OCMEMOG_TRANSCRIPT_POLL_SECONDS:-120}"
  export OCMEMOG_INGEST_BATCH_SECONDS="${OCMEMOG_INGEST_BATCH_SECONDS:-120}"
  export OCMEMOG_INGEST_BATCH_MAX="${OCMEMOG_INGEST_BATCH_MAX:-10}"
  export OCMEMOG_REINFORCE_SENTIMENT="${OCMEMOG_REINFORCE_SENTIMENT:-false}"
else
  export OCMEMOG_TRANSCRIPT_POLL_SECONDS="${OCMEMOG_TRANSCRIPT_POLL_SECONDS:-30}"
  export OCMEMOG_INGEST_BATCH_SECONDS="${OCMEMOG_INGEST_BATCH_SECONDS:-30}"
  export OCMEMOG_INGEST_BATCH_MAX="${OCMEMOG_INGEST_BATCH_MAX:-25}"
  export OCMEMOG_REINFORCE_SENTIMENT="${OCMEMOG_REINFORCE_SENTIMENT:-true}"
fi
export OCMEMOG_INGEST_ENDPOINT="${OCMEMOG_INGEST_ENDPOINT:-http://127.0.0.1:17890/memory/ingest_async}"
export OCMEMOG_INGEST_SOURCE="${OCMEMOG_INGEST_SOURCE:-transcript}"
export OCMEMOG_INGEST_MEMORY_TYPE="${OCMEMOG_INGEST_MEMORY_TYPE:-reflections}"

# promotion/demotion thresholds for stress testing
export OCMEMOG_PROMOTION_THRESHOLD="${OCMEMOG_PROMOTION_THRESHOLD:-0.8}"
export OCMEMOG_DEMOTION_THRESHOLD="${OCMEMOG_DEMOTION_THRESHOLD:-0.4}"

if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
    PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3)"
  fi
fi

exec "${PYTHON_BIN}" -m uvicorn ocmemog.sidecar.app:app --host "${HOST}" --port "${PORT}"
