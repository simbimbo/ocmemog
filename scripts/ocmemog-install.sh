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

is_label_loaded() {
  launchctl print "gui/$UID/$1" >/dev/null 2>&1
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
  rendered_tmp="${rendered}.tmp"
  render_plist "$plist" "$rendered_tmp"
  plutil -lint "$rendered_tmp" >/dev/null
  label=$(basename "$plist" .plist)
  plist_changed="false"
  if [[ -f "$rendered" ]] && cmp -s "$rendered" "$rendered_tmp"; then
    plist_changed="false"
  else
    mv "$rendered_tmp" "$rendered"
    plist_changed="true"
  fi
  rm -f "$rendered_tmp"

  if is_label_loaded "$label"; then
    if [[ "$plist_changed" == "true" ]]; then
      launchctl bootout "gui/$UID/$label" 2>/dev/null || true
      wait_for_label_unloaded "$label" || true
      bootstrap_label "$label" "$rendered"
      launchctl enable "gui/$UID/$label" 2>/dev/null || true
      launchctl kickstart -kp "gui/$UID/$label"
    fi
  else
    bootstrap_label "$label" "$rendered"
    launchctl enable "gui/$UID/$label" 2>/dev/null || true
    launchctl kickstart -kp "gui/$UID/$label"
  fi
  echo "Loaded $label"
done

if ! command -v llama-server >/dev/null 2>&1; then
  echo "llama.cpp not found. Install with: brew install llama.cpp"
  exit 0
fi

echo "Expect local llama.cpp text endpoint at http://127.0.0.1:18080/v1"
echo "Expect local llama.cpp embed endpoint at http://127.0.0.1:18081/v1"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg not found. Install with: brew install ffmpeg"
fi

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "OPENAI_API_KEY not set. Whisper transcription will be disabled."
fi

echo "ocmemog install complete."
