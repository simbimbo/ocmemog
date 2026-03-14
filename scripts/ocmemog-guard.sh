#!/usr/bin/env bash
set -euo pipefail

CONFIG="/Users/simbimbo/.openclaw/openclaw.json"
EXPECTED="memory-ocmemog"

CURRENT=$(jq -r '.plugins.slots.memory // ""' "$CONFIG" 2>/dev/null || echo "")
if [[ "$CURRENT" != "$EXPECTED" ]]; then
  osascript -e "display notification \"Memory slot switched to $CURRENT\" with title \"OpenClaw Memory Alert\""
  echo "ALERT: memory slot is $CURRENT" >> /tmp/ocmemog-guard.alerts.log
fi
