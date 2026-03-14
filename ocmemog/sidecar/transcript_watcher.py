from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional
from urllib import request as urlrequest

DEFAULT_ENDPOINT = "http://127.0.0.1:17890/memory/ingest"
DEFAULT_GLOB = "*.log"


def _pick_latest(path: Path, pattern: str) -> Optional[Path]:
    if path.is_file():
        return path
    if not path.exists():
        return None
    files = sorted(path.glob(pattern), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _post_ingest(endpoint: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(endpoint, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urlrequest.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception:
        return


def watch_forever() -> None:
    transcript_path = os.environ.get("OCMEMOG_TRANSCRIPT_PATH", "").strip()
    transcript_dir = os.environ.get("OCMEMOG_TRANSCRIPT_DIR", "").strip()
    glob_pattern = os.environ.get("OCMEMOG_TRANSCRIPT_GLOB", DEFAULT_GLOB)
    endpoint = os.environ.get("OCMEMOG_INGEST_ENDPOINT", DEFAULT_ENDPOINT)
    poll_seconds = float(os.environ.get("OCMEMOG_TRANSCRIPT_POLL_SECONDS", "1"))
    start_at_end = os.environ.get("OCMEMOG_TRANSCRIPT_START_AT_END", "true").lower() in {"1", "true", "yes"}

    kind = os.environ.get("OCMEMOG_INGEST_KIND", "memory").strip() or "memory"
    source = os.environ.get("OCMEMOG_INGEST_SOURCE", "transcript").strip() or "transcript"
    memory_type = os.environ.get("OCMEMOG_INGEST_MEMORY_TYPE", "knowledge").strip() or "knowledge"

    if transcript_path or transcript_dir:
        target = Path(transcript_path or transcript_dir).expanduser().resolve()
    else:
        target = (Path.home() / ".openclaw" / "workspace" / "memory" / "transcripts").expanduser().resolve()

    current_file: Optional[Path] = None
    position = 0

    while True:
        latest = _pick_latest(target, glob_pattern)
        if latest is None:
            time.sleep(poll_seconds)
            continue

        if current_file is None or latest != current_file:
            current_file = latest
            position = 0
            if start_at_end:
                try:
                    position = current_file.stat().st_size
                except Exception:
                    position = 0

        try:
            with current_file.open("r", encoding="utf-8", errors="ignore") as handle:
                handle.seek(position)
                for line in handle:
                    text = line.rstrip("\n")
                    if not text.strip():
                        continue
                    payload = {
                        "content": text,
                        "kind": kind,
                        "memory_type": memory_type,
                        "source": source,
                    }
                    _post_ingest(endpoint, payload)
                position = handle.tell()
        except Exception:
            time.sleep(poll_seconds)
            continue

        time.sleep(poll_seconds)
