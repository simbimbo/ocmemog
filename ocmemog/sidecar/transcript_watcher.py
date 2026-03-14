from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional
from urllib import request as urlrequest

DEFAULT_ENDPOINT = "http://127.0.0.1:17890/memory/ingest"
DEFAULT_GLOB = "*.log"
DEFAULT_SESSION_GLOB = "*.jsonl"


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


def _extract_user_text(text: str) -> str:
    # Prefer the final user line: "[Sat ...] message"
    candidate = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and "]" in line:
            tail = line.split("]", 1)[-1].strip()
            if tail:
                candidate = tail
    return candidate or text


def _append_transcript(transcripts_dir: Path, timestamp: str, role: str, text: str) -> Path:
    date = timestamp.split("T")[0] if "T" in timestamp else time.strftime("%Y-%m-%d")
    path = transcripts_dir / f"{date}.log"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} [{role}] {text}\n")
    return path


def watch_forever() -> None:
    transcript_path = os.environ.get("OCMEMOG_TRANSCRIPT_PATH", "").strip()
    transcript_dir = os.environ.get("OCMEMOG_TRANSCRIPT_DIR", "").strip()
    glob_pattern = os.environ.get("OCMEMOG_TRANSCRIPT_GLOB", DEFAULT_GLOB)
    session_dir = os.environ.get("OCMEMOG_SESSION_DIR", "").strip()
    session_glob = os.environ.get("OCMEMOG_SESSION_GLOB", DEFAULT_SESSION_GLOB)

    endpoint = os.environ.get("OCMEMOG_INGEST_ENDPOINT", DEFAULT_ENDPOINT)
    poll_seconds = float(os.environ.get("OCMEMOG_TRANSCRIPT_POLL_SECONDS", "1"))
    start_at_end = os.environ.get("OCMEMOG_TRANSCRIPT_START_AT_END", "true").lower() in {"1", "true", "yes"}

    kind = os.environ.get("OCMEMOG_INGEST_KIND", "memory").strip() or "memory"
    source = os.environ.get("OCMEMOG_INGEST_SOURCE", "transcript").strip() or "transcript"
    memory_type = os.environ.get("OCMEMOG_INGEST_MEMORY_TYPE", "knowledge").strip() or "knowledge"

    if transcript_path or transcript_dir:
        transcript_target = Path(transcript_path or transcript_dir).expanduser().resolve()
    else:
        transcript_target = (Path.home() / ".openclaw" / "workspace" / "memory" / "transcripts").expanduser().resolve()

    if session_dir:
        session_target = Path(session_dir).expanduser().resolve()
    else:
        session_target = (Path.home() / ".openclaw" / "agents" / "main" / "sessions").expanduser().resolve()

    current_file: Optional[Path] = None
    position = 0
    session_file: Optional[Path] = None
    session_pos = 0

    while True:
        # 1) Watch transcript logs (if any)
        latest = _pick_latest(transcript_target, glob_pattern)
        if latest is not None:
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
                pass

        # 2) Watch OpenClaw session jsonl (verbatim capture)
        session_latest = _pick_latest(session_target, session_glob)
        if session_latest is not None:
            if session_file is None or session_latest != session_file:
                session_file = session_latest
                session_pos = 0
                if start_at_end:
                    try:
                        session_pos = session_file.stat().st_size
                    except Exception:
                        session_pos = 0
            try:
                with session_file.open("r", encoding="utf-8", errors="ignore") as handle:
                    handle.seek(session_pos)
                    for line in handle:
                        try:
                            entry = json.loads(line)
                        except Exception:
                            continue
                        if entry.get("type") != "message":
                            continue
                        msg = entry.get("message") or {}
                        role = msg.get("role")
                        if role not in {"user", "assistant"}:
                            continue
                        content = msg.get("content")
                        if isinstance(content, list):
                            text = next((c.get("text") for c in content if c.get("type") == "text"), "")
                        else:
                            text = content or ""
                        text = str(text).strip()
                        if role == "user":
                            text = _extract_user_text(text)
                        text = text.replace("\n", " ").strip()
                        if not text:
                            continue
                        timestamp = entry.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%S")
                        transcript_path = _append_transcript(transcript_target, timestamp, role, text)
                        payload = {
                            "content": text,
                            "kind": kind,
                            "memory_type": memory_type,
                            "source": "session",
                            "transcript_path": str(transcript_path),
                            "timestamp": timestamp.replace("T", " ")[:19],
                        }
                        _post_ingest(endpoint, payload)
                    session_pos = handle.tell()
            except Exception:
                pass

        time.sleep(poll_seconds)
