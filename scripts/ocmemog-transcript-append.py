#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
import sys
import os

def _default_openclaw_home() -> Path:
    explicit = (os.environ.get("OPENCLAW_HOME") or os.environ.get("OCMEMOG_OPENCLAW_HOME") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg:
        return (Path(xdg).expanduser() / "openclaw").resolve()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "").strip() or os.environ.get("LOCALAPPDATA", "").strip()
        if appdata:
            return (Path(appdata).expanduser() / "OpenClaw").resolve()
    return (Path.home() / ".openclaw").resolve()


ROOT = (_default_openclaw_home() / "workspace" / "memory" / "transcripts").resolve()
STATE = ROOT / "transcript-state.json"
ROOT.mkdir(parents=True, exist_ok=True)

payload = json.loads(sys.stdin.read() or "{}")
messages = payload.get("messages", [])

if not messages:
    sys.exit(0)

# determine log file by date of newest message
latest_ts = None
for m in messages:
    ts = m.get("timestamp") or m.get("createdAt")
    if ts:
        latest_ts = ts

if latest_ts:
    try:
        dt = datetime.fromisoformat(latest_ts.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.utcnow()
else:
    dt = datetime.utcnow()

log_path = ROOT / f"{dt.strftime('%Y-%m-%d')}.log"

with log_path.open("a", encoding="utf-8") as handle:
    for m in messages:
        role = m.get("role", "")
        content = (m.get("content") or "").replace("\n", " ")
        ts = m.get("timestamp") or m.get("createdAt") or ""
        handle.write(f"{ts} [{role}] {content}\n")

# update state with last message id
last_id = messages[-1].get("id") or messages[-1].get("message_id")
STATE.write_text(json.dumps({"last_id": last_id, "updated": datetime.utcnow().isoformat()}), encoding="utf-8")
