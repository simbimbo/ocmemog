#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${1:-$ROOT_DIR}"
REPO_URL="${OCMEMOG_REPO_URL:-https://github.com/simbimbo/ocmemog.git}"
PLUGIN_PACKAGE="@openclaw/memory-ocmemog"
PLUGIN_ID="memory-ocmemog"
ENDPOINT="${OCMEMOG_ENDPOINT:-http://127.0.0.1:17890}"
TIMEOUT_MS="${OCMEMOG_TIMEOUT_MS:-30000}"
DEFAULT_OLLAMA_MODEL="${OCMEMOG_OLLAMA_MODEL:-phi3:latest}"
DEFAULT_OLLAMA_EMBED_MODEL="${OCMEMOG_OLLAMA_EMBED_MODEL:-nomic-embed-text:latest}"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

log() {
  printf '[ocmemog-install] %s\n' "$*"
}

warn() {
  printf '[ocmemog-install] WARN: %s\n' "$*" >&2
}

have() {
  command -v "$1" >/dev/null 2>&1
}

ensure_repo() {
  if [[ "$TARGET_DIR" == "$ROOT_DIR" ]]; then
    log "Using existing repo at $TARGET_DIR"
    return
  fi
  if [[ -d "$TARGET_DIR/.git" ]]; then
    log "Updating existing checkout at $TARGET_DIR"
    git -C "$TARGET_DIR" pull --ff-only
  else
    log "Cloning $REPO_URL to $TARGET_DIR"
    git clone "$REPO_URL" "$TARGET_DIR"
  fi
  ROOT_DIR="$TARGET_DIR"
}

ensure_python() {
  if ! have python3; then
    warn "python3 is required but not installed"
    exit 1
  fi
  if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
    log "Creating virtualenv"
    python3 -m venv "$ROOT_DIR/.venv"
  fi
  log "Installing Python requirements"
  "$ROOT_DIR/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
  "$ROOT_DIR/.venv/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt"
}

install_plugin() {
  if ! have openclaw; then
    warn "openclaw CLI not found; skipping plugin install/enable"
    return
  fi
  log "Installing/enabling OpenClaw plugin if needed"
  if openclaw plugins install "$PLUGIN_PACKAGE" >/dev/null 2>&1; then
    log "Installed plugin package $PLUGIN_PACKAGE"
  else
    warn "Package install failed or package unavailable here; falling back to local path install"
    openclaw plugins install -l "$ROOT_DIR"
  fi
  openclaw plugins enable "$PLUGIN_ID" || warn "Could not enable plugin automatically"
}

install_launchagents() {
  if [[ ! -x "$ROOT_DIR/scripts/ocmemog-install.sh" ]]; then
    warn "LaunchAgent installer missing at scripts/ocmemog-install.sh"
    return
  fi
  log "Installing LaunchAgents"
  "$ROOT_DIR/scripts/ocmemog-install.sh"
}

ensure_ollama_models() {
  if ! have ollama; then
    warn "Ollama not found. Install from https://ollama.com/download to enable local models."
    return
  fi
  if ! ollama list | rg -q "$(printf '%s' "$DEFAULT_OLLAMA_MODEL" | sed 's/:.*$//')"; then
    log "Pulling local model $DEFAULT_OLLAMA_MODEL"
    ollama pull "$DEFAULT_OLLAMA_MODEL"
  fi
  if ! ollama list | rg -q "$(printf '%s' "$DEFAULT_OLLAMA_EMBED_MODEL" | sed 's/:.*$//')"; then
    log "Pulling local embed model $DEFAULT_OLLAMA_EMBED_MODEL"
    ollama pull "$DEFAULT_OLLAMA_EMBED_MODEL"
  fi
}

validate_install() {
  if ! have curl; then
    warn "curl not found; skipping health check"
    return
  fi
  log "Waiting for sidecar health check at $ENDPOINT/healthz"
  for _ in {1..20}; do
    if curl -fsS --max-time 3 "$ENDPOINT/healthz" >/dev/null 2>&1; then
      log "Sidecar is healthy"
      return
    fi
    sleep 1
  done
  warn "Sidecar health check did not pass yet"
}

print_summary() {
  cat <<EOF

ocmemog install summary
- repo: $ROOT_DIR
- endpoint: $ENDPOINT
- timeoutMs: $TIMEOUT_MS
- local model: $DEFAULT_OLLAMA_MODEL
- embed model: $DEFAULT_OLLAMA_EMBED_MODEL

Next checks:
- openclaw plugins
- curl $ENDPOINT/healthz
- openclaw status --deep
EOF
}

ensure_repo
ensure_python
install_plugin
install_launchagents
ensure_ollama_models
validate_install
print_summary
