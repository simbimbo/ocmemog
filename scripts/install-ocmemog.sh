#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${ROOT_DIR}"
REPO_URL="${OCMEMOG_REPO_URL:-https://github.com/simbimbo/ocmemog.git}"
PLUGIN_PACKAGE="@simbimbo/memory-ocmemog"
PLUGIN_ID="memory-ocmemog"
ENDPOINT="${OCMEMOG_ENDPOINT:-http://127.0.0.1:17890}"
TIMEOUT_MS="${OCMEMOG_TIMEOUT_MS:-30000}"
DEFAULT_OLLAMA_MODEL="${OCMEMOG_OLLAMA_MODEL:-phi3:latest}"
DEFAULT_OLLAMA_EMBED_MODEL="${OCMEMOG_OLLAMA_EMBED_MODEL:-nomic-embed-text:latest}"
INSTALL_PREREQS="${OCMEMOG_INSTALL_PREREQS:-false}"
SKIP_PLUGIN_INSTALL="false"
SKIP_LAUNCHAGENTS="false"
SKIP_MODEL_PULLS="false"
DRY_RUN="false"

usage() {
  cat <<'EOF'
Usage: scripts/install-ocmemog.sh [target-dir] [options]

Install/configure ocmemog for local OpenClaw use.

Arguments:
  target-dir                 Optional clone/update target directory.

Options:
  --help                     Show this help text.
  --install-prereqs          Auto-install missing ollama/ffmpeg via Homebrew.
  --skip-plugin-install      Skip OpenClaw plugin install/enable.
  --skip-launchagents        Skip LaunchAgent install/load.
  --skip-model-pulls         Skip local Ollama model pulls.
  --dry-run                  Print what would happen without making changes.
  --endpoint URL             Override sidecar endpoint (default: http://127.0.0.1:17890).
  --timeout-ms N             Override plugin timeout summary value (default: 30000).
  --repo-url URL             Override git clone/update source.

Environment:
  OCMEMOG_INSTALL_PREREQS=true   Same as --install-prereqs.
  OCMEMOG_OLLAMA_MODEL           Default local model to pull.
  OCMEMOG_OLLAMA_EMBED_MODEL     Default local embedding model to pull.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    --install-prereqs)
      INSTALL_PREREQS="true"
      shift
      ;;
    --skip-plugin-install)
      SKIP_PLUGIN_INSTALL="true"
      shift
      ;;
    --skip-launchagents)
      SKIP_LAUNCHAGENTS="true"
      shift
      ;;
    --skip-model-pulls)
      SKIP_MODEL_PULLS="true"
      shift
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    --endpoint)
      ENDPOINT="$2"
      shift 2
      ;;
    --timeout-ms)
      TIMEOUT_MS="$2"
      shift 2
      ;;
    --repo-url)
      REPO_URL="$2"
      shift 2
      ;;
    --*)
      printf 'Unknown option: %s\n\n' "$1" >&2
      usage >&2
      exit 1
      ;;
    *)
      TARGET_DIR="$1"
      shift
      ;;
  esac
done

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

run_cmd() {
  if [[ "$DRY_RUN" == "true" ]]; then
    printf '[ocmemog-install] DRY RUN: '
    printf '%q ' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

maybe_install_prereqs() {
  if [[ "$INSTALL_PREREQS" != "true" ]]; then
    return
  fi
  if ! have brew; then
    warn "Homebrew not found; cannot auto-install prerequisites"
    return
  fi
  if ! have ollama; then
    log "Installing Ollama via Homebrew"
    run_cmd brew install ollama || warn "brew install ollama failed"
  fi
  if ! have ffmpeg; then
    log "Installing ffmpeg via Homebrew"
    run_cmd brew install ffmpeg || warn "brew install ffmpeg failed"
  fi
}

ensure_repo() {
  if [[ "$TARGET_DIR" == "$ROOT_DIR" ]]; then
    log "Using existing repo at $TARGET_DIR"
    return
  fi
  if [[ -d "$TARGET_DIR/.git" ]]; then
    log "Updating existing checkout at $TARGET_DIR"
    run_cmd git -C "$TARGET_DIR" pull --ff-only
  else
    log "Cloning $REPO_URL to $TARGET_DIR"
    run_cmd git clone "$REPO_URL" "$TARGET_DIR"
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
    run_cmd python3 -m venv "$ROOT_DIR/.venv"
  fi
  if [[ "$DRY_RUN" == "true" ]]; then
    log "Would install Python requirements into $ROOT_DIR/.venv"
    return
  fi
  log "Installing Python requirements"
  "$ROOT_DIR/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
  "$ROOT_DIR/.venv/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt"
}

install_plugin() {
  if [[ "$SKIP_PLUGIN_INSTALL" == "true" ]]; then
    log "Skipping plugin install/enable by request"
    return
  fi
  if ! have openclaw; then
    warn "openclaw CLI not found; skipping plugin install/enable"
    return
  fi
  log "Installing/enabling OpenClaw plugin if needed"
  if [[ "$DRY_RUN" == "true" ]]; then
    log "Would attempt package install: openclaw plugins install $PLUGIN_PACKAGE"
    log "Would fall back to local path install if needed: openclaw plugins install -l $ROOT_DIR"
    log "Would enable plugin: openclaw plugins enable $PLUGIN_ID"
    return
  fi
  if openclaw plugins install "$PLUGIN_PACKAGE" >/dev/null 2>&1; then
    log "Installed plugin package $PLUGIN_PACKAGE"
  else
    warn "Package install failed or package unavailable here; falling back to local path install"
    openclaw plugins install -l "$ROOT_DIR"
  fi
  openclaw plugins enable "$PLUGIN_ID" || warn "Could not enable plugin automatically"
}

install_launchagents() {
  if [[ "$SKIP_LAUNCHAGENTS" == "true" ]]; then
    log "Skipping LaunchAgent install/load by request"
    return
  fi
  if [[ ! -x "$ROOT_DIR/scripts/ocmemog-install.sh" ]]; then
    warn "LaunchAgent installer missing at scripts/ocmemog-install.sh"
    return
  fi
  log "Installing LaunchAgents"
  run_cmd "$ROOT_DIR/scripts/ocmemog-install.sh"
}

ensure_ollama_models() {
  if [[ "$SKIP_MODEL_PULLS" == "true" ]]; then
    log "Skipping local model pulls by request"
    return
  fi
  if ! have ollama; then
    warn "Ollama not found. Install from https://ollama.com/download to enable local models."
    return
  fi
  if ! ollama list | rg -q "$(printf '%s' "$DEFAULT_OLLAMA_MODEL" | sed 's/:.*$//')"; then
    log "Pulling local model $DEFAULT_OLLAMA_MODEL"
    run_cmd ollama pull "$DEFAULT_OLLAMA_MODEL"
  fi
  if ! ollama list | rg -q "$(printf '%s' "$DEFAULT_OLLAMA_EMBED_MODEL" | sed 's/:.*$//')"; then
    log "Pulling local embed model $DEFAULT_OLLAMA_EMBED_MODEL"
    run_cmd ollama pull "$DEFAULT_OLLAMA_EMBED_MODEL"
  fi
}

validate_install() {
  if ! have curl; then
    warn "curl not found; skipping health check"
    return
  fi
  if [[ "$DRY_RUN" == "true" ]]; then
    log "Would validate sidecar health at $ENDPOINT/healthz"
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
- install prereqs automatically: $INSTALL_PREREQS
- skip plugin install: $SKIP_PLUGIN_INSTALL
- skip LaunchAgents: $SKIP_LAUNCHAGENTS
- skip model pulls: $SKIP_MODEL_PULLS
- dry run: $DRY_RUN

Next checks:
- openclaw plugins
- curl $ENDPOINT/healthz
- openclaw status --deep
EOF
}

ensure_repo
maybe_install_prereqs
ensure_python
install_plugin
install_launchagents
ensure_ollama_models
validate_install
print_summary
