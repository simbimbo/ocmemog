#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LA_DIR="$HOME/Library/LaunchAgents"

mkdir -p "$LA_DIR"

for plist in "$ROOT_DIR"/scripts/launchagents/com.openclaw.ocmemog.{sidecar,ponder,guard}.plist; do
  cp "$plist" "$LA_DIR/"
  label=$(basename "$plist" .plist)
  launchctl bootout gui/$UID/$label 2>/dev/null || true
  launchctl bootstrap gui/$UID "$LA_DIR/$(basename "$plist")"
  launchctl kickstart -k gui/$UID/$label
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

echo "ocmemog install complete."
