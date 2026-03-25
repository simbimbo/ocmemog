#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${OCMEMOG_HOST:-127.0.0.1}"
PORT="${OCMEMOG_PORT:-17891}"
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

# defaults for local llama.cpp / OpenAI-compatible inference and embeddings
export OCMEMOG_USE_OLLAMA="${OCMEMOG_USE_OLLAMA:-false}"
export OCMEMOG_LOCAL_LLM_BASE_URL="${OCMEMOG_LOCAL_LLM_BASE_URL:-http://127.0.0.1:18080/v1}"
export OCMEMOG_LOCAL_LLM_MODEL="${OCMEMOG_LOCAL_LLM_MODEL:-qwen2.5-7b-instruct}"
export OCMEMOG_LOCAL_EMBED_BASE_URL="${OCMEMOG_LOCAL_EMBED_BASE_URL:-http://127.0.0.1:18081/v1}"
export OCMEMOG_LOCAL_EMBED_MODEL="${OCMEMOG_LOCAL_EMBED_MODEL:-nomic-embed-text-v1.5}"
export OCMEMOG_OLLAMA_MODEL="${OCMEMOG_OLLAMA_MODEL:-qwen2.5:7b}"
export OCMEMOG_OLLAMA_EMBED_MODEL="${OCMEMOG_OLLAMA_EMBED_MODEL:-nomic-embed-text:latest}"
export OCMEMOG_PONDER_MODEL="${OCMEMOG_PONDER_MODEL:-local-openai:qwen2.5-7b-instruct}"
export OCMEMOG_EMBED_MODEL_PROVIDER="${OCMEMOG_EMBED_MODEL_PROVIDER:-${BRAIN_EMBED_MODEL_PROVIDER:-local-openai}}"
export OCMEMOG_EMBED_MODEL_LOCAL="${OCMEMOG_EMBED_MODEL_LOCAL:-${BRAIN_EMBED_MODEL_LOCAL:-}}"
export BRAIN_EMBED_MODEL_PROVIDER="${BRAIN_EMBED_MODEL_PROVIDER:-${OCMEMOG_EMBED_MODEL_PROVIDER}}"
export BRAIN_EMBED_MODEL_LOCAL="${BRAIN_EMBED_MODEL_LOCAL:-${OCMEMOG_EMBED_MODEL_LOCAL}}"

# battery-aware transcript watcher defaults
default_openclaw_home() {
  if [[ -n "${OPENCLAW_HOME:-}" ]]; then
    printf '%s\n' "$OPENCLAW_HOME"
    return
  fi
  if [[ -n "${OCMEMOG_OPENCLAW_HOME:-}" ]]; then
    printf '%s\n' "$OCMEMOG_OPENCLAW_HOME"
    return
  fi
  if [[ -n "${XDG_DATA_HOME:-}" ]]; then
    printf '%s\n' "$XDG_DATA_HOME/openclaw"
    return
  fi
  if [[ "$(uname -s)" =~ ^(MINGW|MSYS|CYGWIN|Windows_NT)$ ]] && [[ -n "${APPDATA:-${LOCALAPPDATA:-}}" ]]; then
    printf '%s\n' "${APPDATA:-${LOCALAPPDATA}}/OpenClaw"
    return
  fi
  printf '%s\n' "$HOME/.openclaw"
}

OPENCLAW_HOME_DIR="$(default_openclaw_home)"
export OCMEMOG_TRANSCRIPT_WATCHER="${OCMEMOG_TRANSCRIPT_WATCHER:-true}"
export OCMEMOG_TRANSCRIPT_DIR="${OCMEMOG_TRANSCRIPT_DIR:-$OPENCLAW_HOME_DIR/workspace/memory/transcripts}"
export OCMEMOG_SESSION_DIR="${OCMEMOG_SESSION_DIR:-$OPENCLAW_HOME_DIR/agents/main/sessions}"
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
export OCMEMOG_INGEST_ENDPOINT="${OCMEMOG_INGEST_ENDPOINT:-http://127.0.0.1:17891/memory/ingest_async}"
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

exec -a ocmemog-sidecar "${PYTHON_BIN}" -m uvicorn ocmemog.sidecar.app:app --host "${HOST}" --port "${PORT}"
