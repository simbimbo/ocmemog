#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LA_DIR="$HOME/Library/LaunchAgents"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

mkdir -p "$LA_DIR"

render_plist() {
  local src="$1"
  local dest="$2"
  python3 - "$src" "$dest" "$ROOT_DIR" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
dest = Path(sys.argv[2])
root = sys.argv[3]
dest.write_text(src.read_text(encoding="utf-8").replace("__ROOT_DIR__", root), encoding="utf-8")
PY
}

wait_for_label_unloaded() {
  local label="$1"
  local attempts="${2:-25}"
  local sleep_s="${3:-0.2}"
  for ((i=0; i<attempts; i++)); do
    if ! launchctl print "gui/$UID/$label" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$sleep_s"
  done
  return 1
}

bootstrap_label() {
  local label="$1"
  local rendered="$2"
  local last_output=""
  for attempt in 1 2 3; do
    if last_output=$(launchctl bootstrap "gui/$UID" "$rendered" 2>&1); then
      return 0
    fi
    if [[ "$last_output" == *"Input/output error"* ]]; then
      sleep 1
      continue
    fi
    printf '%s\n' "$last_output" >&2
    return 1
  done
  printf '%s\n' "$last_output" >&2
  return 1
}

for plist in "$ROOT_DIR"/scripts/launchagents/com.openclaw.ocmemog.{sidecar,ponder,guard}.plist; do
  rendered="$LA_DIR/$(basename "$plist")"
  render_plist "$plist" "$rendered"
  plutil -lint "$rendered" >/dev/null
  label=$(basename "$plist" .plist)
  launchctl bootout "gui/$UID/$label" 2>/dev/null || true
  wait_for_label_unloaded "$label" || true
  bootstrap_label "$label" "$rendered"
  launchctl enable "gui/$UID/$label" 2>/dev/null || true
  launchctl kickstart -kp "gui/$UID/$label"
  echo "Loaded $label"
done

if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama not found. Install from: https://ollama.com/download"
  echo "Then run: ollama pull phi3 && ollama pull nomic-embed-text"
  exit 0
fi

if ! ollama list | rg -q "phi3"; then
  echo "Pulling phi3..."
  ollama pull phi3
fi

if ! ollama list | rg -q "nomic-embed-text"; then
  echo "Pulling nomic-embed-text..."
  ollama pull nomic-embed-text
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg not found. Install with: brew install ffmpeg"
fi

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "OPENAI_API_KEY not set. Whisper transcription will be disabled."
fi

echo "ocmemog install complete."
