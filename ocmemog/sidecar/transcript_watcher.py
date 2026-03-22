from __future__ import annotations

import json
import sys
import os
import time
from collections import deque
import threading
from pathlib import Path
from typing import Optional
from urllib import request as urlrequest

from ocmemog.runtime import state_store

DEFAULT_ENDPOINT = "http://127.0.0.1:17891/memory/ingest_async"
DEFAULT_GLOB = "*.log"
DEFAULT_SESSION_GLOB = "*.jsonl"
DEFAULT_REINFORCE_POSITIVE = [
    "good job",
    "nice job",
    "well done",
    "great work",
    "awesome",
    "thanks",
    "thank you",
    "love it",
]
DEFAULT_REINFORCE_NEGATIVE = [
    "not good",
    "bad job",
    "this sucks",
    "terrible",
    "awful",
    "wrong",
    "disappointed",
    "frustrated",
]
WATCHER_ERROR_LOG = state_store.reports_dir() / "ocmemog_transcript_watcher_errors.jsonl"
_SHUTDOWN_TRACE = os.environ.get("OCMEMOG_SHUTDOWN_TIMING", "true").lower() in {"1", "true", "yes", "on"}
_WATCHER_REQUEST_TIMEOUT_SECONDS = 10.0
_WATCHER_SHUTDOWN_REQUEST_TIMEOUT_SECONDS = 1.0
_WATCHER_STOP_EVENT: threading.Event | None = None

try:
    _WATCHER_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("OCMEMOG_INGEST_REQUEST_TIMEOUT_SECONDS", "10"))
except Exception:
    pass
try:
    _WATCHER_SHUTDOWN_REQUEST_TIMEOUT_SECONDS = float(
        os.environ.get("OCMEMOG_SHUTDOWN_INGEST_REQUEST_TIMEOUT_SECONDS", "1")
    )
except Exception:
    pass


def _watcher_timeout(stop_event: threading.Event | None) -> float:
    timeout = _WATCHER_REQUEST_TIMEOUT_SECONDS
    if stop_event is not None and stop_event.is_set():
        timeout = min(timeout, _WATCHER_SHUTDOWN_REQUEST_TIMEOUT_SECONDS)
    return max(0.05, timeout)


def _post_json_payload(endpoint: str, payload: dict, *, stop_event: threading.Event | None, kind: str) -> bool:
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(endpoint, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    _apply_auth_headers(req)
    timeout = _watcher_timeout(stop_event)
    start = time.perf_counter()
    status = "ok"
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            resp.read()
        return True
    except Exception as exc:
        status = f"error={type(exc).__name__}"
        _log_watcher_error(kind, endpoint, payload, exc)
        if _SHUTDOWN_TRACE:
            print(
                f"[ocmemog][watcher-request] {kind} failed timeout={timeout:.3f}s elapsed={time.perf_counter()-start:.3f}s",
                file=sys.stderr,
            )
        return False
    finally:
        if _SHUTDOWN_TRACE:
            elapsed = time.perf_counter() - start
            if stop_event is None or not stop_event.is_set():
                if elapsed >= timeout * 0.95:
                    print(
                        f"[ocmemog][watcher-request] {kind} timeout={timeout:.3f}s elapsed={elapsed:.3f}s status={status}",
                        file=sys.stderr,
                    )
            else:
                print(
                    f"[ocmemog][watcher-request] {kind} timeout={timeout:.3f}s elapsed={elapsed:.3f}s status={status}",
                    file=sys.stderr,
                )


def _log_watcher_error(kind: str, endpoint: str, payload: dict, exc: Exception) -> None:
    try:
        WATCHER_ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with WATCHER_ERROR_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "kind": kind,
                "endpoint": endpoint,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "payload_preview": str(payload)[:500],
            }, ensure_ascii=False) + "\n")
    except Exception:
        return


def _pick_latest(path: Path, pattern: str) -> Optional[Path]:
    if path.is_file():
        return path
    if not path.exists():
        return None
    files = []
    for candidate in path.glob(pattern):
        try:
            mtime = candidate.stat().st_mtime
        except FileNotFoundError:
            continue
        files.append((mtime, candidate))
    files.sort(key=lambda item: item[0])
    return files[-1][1] if files else None


def _apply_auth_headers(req: urlrequest.Request) -> None:
    token = os.environ.get("OCMEMOG_API_TOKEN", "").strip()
    if token:
        req.add_header("x-ocmemog-token", token)


def _post_ingest(endpoint: str, payload: dict, *, stop_event: threading.Event | None = None) -> bool:
    return _post_json_payload(endpoint, payload, stop_event=stop_event, kind="ingest")


def _post_json(endpoint: str, payload: dict, *, stop_event: threading.Event | None = None) -> bool:
    return _post_json_payload(endpoint, payload, stop_event=stop_event, kind="json")


def _post_turn(endpoint: str, payload: dict, *, stop_event: threading.Event | None = None) -> bool:
    return _post_json(endpoint, payload, stop_event=stop_event)


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


def _extract_conversation_info(text: str) -> dict:
    marker = "Conversation info (untrusted metadata):"
    if marker not in text:
        return {}
    tail = text.split(marker, 1)[1]
    start = tail.find("```")
    if start < 0:
        return {}
    tail = tail[start + 3 :]
    if tail.startswith("json"):
        tail = tail[4:]
    end = tail.find("```")
    if end < 0:
        return {}
    raw = tail[:end].strip()
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_message_text(content: object) -> str:
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "text":
                continue
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
        return "\n".join(parts)
    return str(content or "")


def _parse_transcript_line(text: str) -> tuple[Optional[str], str]:
    stripped = text.strip()
    if not stripped:
        return None, ""
    if "[" in stripped and "]" in stripped:
        prefix, suffix = stripped.split("[", 1)
        role_part, remainder = suffix.split("]", 1)
        role = role_part.strip().lower()
        if role in {"user", "assistant", "system", "tool"}:
            return role, remainder.strip()
    return None, stripped


def _count_lines(path: Path) -> int:
    if not path.exists() or not path.is_file():
        return 0
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return sum(1 for _ in handle)



def _append_transcript(transcript_target: Path, timestamp: str, role: str, text: str) -> tuple[Path, int]:
    if transcript_target.suffix:
        path = transcript_target
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        date = timestamp.split("T")[0] if "T" in timestamp else time.strftime("%Y-%m-%d")
        path = transcript_target / f"{date}.log"
        transcript_target.mkdir(parents=True, exist_ok=True)
    line_no = _count_lines(path) + 1
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} [{role}] {text}\n")
    return path, line_no


def watch_forever(stop_event: Optional[threading.Event] = None) -> None:
    global _WATCHER_STOP_EVENT
    transcript_path = os.environ.get("OCMEMOG_TRANSCRIPT_PATH", "").strip()
    transcript_dir = os.environ.get("OCMEMOG_TRANSCRIPT_DIR", "").strip()
    glob_pattern = os.environ.get("OCMEMOG_TRANSCRIPT_GLOB", DEFAULT_GLOB)
    session_dir = os.environ.get("OCMEMOG_SESSION_DIR", "").strip()
    session_glob = os.environ.get("OCMEMOG_SESSION_GLOB", DEFAULT_SESSION_GLOB)

    endpoint = os.environ.get("OCMEMOG_INGEST_ENDPOINT", DEFAULT_ENDPOINT)
    turn_endpoint = os.environ.get("OCMEMOG_TURN_INGEST_ENDPOINT", endpoint.replace("/memory/ingest_async", "/conversation/ingest_turn").replace("/memory/ingest", "/conversation/ingest_turn"))
    poll_seconds = float(os.environ.get("OCMEMOG_TRANSCRIPT_POLL_SECONDS", "30"))
    batch_seconds = float(os.environ.get("OCMEMOG_INGEST_BATCH_SECONDS", "30"))
    batch_max = int(os.environ.get("OCMEMOG_INGEST_BATCH_MAX", "25"))
    start_at_end = os.environ.get("OCMEMOG_TRANSCRIPT_START_AT_END", "true").lower() in {"1", "true", "yes"}

    kind = os.environ.get("OCMEMOG_INGEST_KIND", "memory").strip() or "memory"
    source = os.environ.get("OCMEMOG_INGEST_SOURCE", "transcript").strip() or "transcript"
    memory_type = os.environ.get("OCMEMOG_INGEST_MEMORY_TYPE", "knowledge").strip() or "knowledge"

    reinforce_enabled = os.environ.get("OCMEMOG_REINFORCE_SENTIMENT", "true").lower() in {"1", "true", "yes"}
    reinforce_endpoint = os.environ.get(
        "OCMEMOG_REINFORCE_ENDPOINT", "http://127.0.0.1:17891/memory/reinforce"
    ).strip()
    pos_terms = os.environ.get("OCMEMOG_REINFORCE_POSITIVE", ",".join(DEFAULT_REINFORCE_POSITIVE))
    neg_terms = os.environ.get("OCMEMOG_REINFORCE_NEGATIVE", ",".join(DEFAULT_REINFORCE_NEGATIVE))
    positive_terms = [t.strip().lower() for t in pos_terms.split(",") if t.strip()]
    negative_terms = [t.strip().lower() for t in neg_terms.split(",") if t.strip()]

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
    current_line_number = 0
    session_file: Optional[Path] = None
    session_pos = 0

    transcript_buffer: list[str] = []
    session_buffer: list[str] = []
    transcript_last_path: Optional[Path] = None
    session_last_path: Optional[Path] = None
    transcript_last_timestamp: Optional[str] = None
    session_last_timestamp: Optional[str] = None
    transcript_start_line: Optional[int] = None
    transcript_end_line: Optional[int] = None
    session_start_line: Optional[int] = None
    session_end_line: Optional[int] = None
    recent_session_transcript_lines: deque[tuple[str, int]] = deque(maxlen=max(batch_max * 8, 128))
    pending_session_turns: dict[tuple[str, int], dict[str, object]] = {}
    last_transcript_flush = time.time()
    last_session_flush = time.time()
    stopper: threading.Event
    if isinstance(stop_event, threading.Event):
        stopper = stop_event
    else:
        stopper = threading.Event()
        stopper.clear()
    _WATCHER_STOP_EVENT = stopper

    def _flush_buffer(
        buffer: list[str],
        *,
        source_label: str,
        transcript_path: Optional[Path],
        timestamp: Optional[str],
        start_line: Optional[int],
        end_line: Optional[int],
        stop_event: threading.Event,
    ) -> bool:
        if not buffer:
            return True
        if stop_event.is_set():
            return False
        payload = {
            "content": "\n".join(buffer),
            "kind": kind,
            "memory_type": memory_type,
            "source": source_label,
        }
        if transcript_path is not None:
            payload["transcript_path"] = str(transcript_path)
        if start_line is not None:
            payload["transcript_offset"] = start_line
        if end_line is not None:
            payload["transcript_end_offset"] = end_line
        if timestamp:
            payload["timestamp"] = timestamp.replace("T", " ")[:19]
        ok = _post_ingest(endpoint, payload, stop_event=stop_event)
        if ok:
            buffer.clear()
        return ok

    def _maybe_reinforce(text: str, timestamp: str) -> None:
        if not reinforce_enabled:
            return
        lowered = text.lower()
        if any(term in lowered for term in positive_terms):
            payload = {
                "task_id": f"feedback:{timestamp}",
                "outcome": "positive feedback",
                "reward_score": 1.0,
                "confidence": 1.0,
                "memory_reference": "feedback:chat",
                "experience_type": "reinforcement",
                "source_module": "sentiment",
                "note": text,
            }
            _post_json(reinforce_endpoint, payload, stop_event=stopper)
        elif any(term in lowered for term in negative_terms):
            payload = {
                "task_id": f"feedback:{timestamp}",
                "outcome": "negative feedback",
                "reward_score": 0.0,
                "confidence": 1.0,
                "memory_reference": "feedback:chat",
                "experience_type": "reinforcement",
                "source_module": "sentiment",
                "note": text,
            }
            _post_json(reinforce_endpoint, payload, stop_event=stopper)

    try:
        while not stopper.is_set():
            # 1) Watch transcript logs (if any)
            latest = _pick_latest(transcript_target, glob_pattern)
            if latest is not None:
                if current_file is None or latest != current_file:
                    current_file = latest
                    position = 0
                    current_line_number = 0
                    if start_at_end:
                        try:
                            position = current_file.stat().st_size
                        except Exception:
                            position = 0
                        try:
                            current_line_number = _count_lines(current_file)
                        except Exception:
                            current_line_number = 0

                try:
                    with current_file.open("r", encoding="utf-8", errors="ignore") as handle:
                        handle.seek(position)
                        committed_position = position
                        committed_line_number = current_line_number
                        while True:
                            if stopper.is_set():
                                break
                            line_start = handle.tell()
                            line = handle.readline()
                            if not line:
                                position = committed_position
                                current_line_number = committed_line_number
                                break
                            text = line.rstrip("\n")
                            next_line_number = committed_line_number + 1
                            if not text.strip():
                                committed_position = handle.tell()
                                committed_line_number = next_line_number
                                position = committed_position
                                current_line_number = committed_line_number
                                continue
                            current_marker = (str(current_file), next_line_number)
                            if current_marker in recent_session_transcript_lines:
                                committed_position = handle.tell()
                                committed_line_number = next_line_number
                                position = committed_position
                                current_line_number = committed_line_number
                                continue
                            transcript_buffer.append(text)
                            transcript_last_path = current_file
                            if transcript_start_line is None:
                                transcript_start_line = next_line_number
                            transcript_end_line = next_line_number
                            timestamp_value = None
                            if text and " " in text:
                                timestamp_value = text.split(" ", 1)[0]
                                transcript_last_timestamp = timestamp_value
                            role, turn_text = _parse_transcript_line(text)
                            if role and turn_text:
                                if stopper.is_set():
                                    break
                                ok = _post_turn(
                                    turn_endpoint,
                                    {
                                        "role": role,
                                        "content": turn_text,
                                        "source": source,
                                        "transcript_path": str(current_file),
                                        "transcript_offset": next_line_number,
                                        "transcript_end_offset": next_line_number,
                                        "timestamp": timestamp_value.replace("T", " ")[:19] if timestamp_value else None,
                                    },
                                    stop_event=stopper,
                                )
                                if not ok:
                                    if transcript_buffer:
                                        transcript_buffer.pop()
                                    if transcript_start_line == next_line_number:
                                        transcript_start_line = None
                                    transcript_end_line = committed_line_number if transcript_start_line is not None else None
                                    position = line_start
                                    current_line_number = committed_line_number
                                    break
                            if len(transcript_buffer) >= batch_max:
                                ok = _flush_buffer(
                                    transcript_buffer,
                                    source_label=source,
                                    transcript_path=transcript_last_path,
                                    timestamp=transcript_last_timestamp,
                                    start_line=transcript_start_line,
                                    end_line=transcript_end_line,
                                    stop_event=stopper,
                                )
                                if not ok:
                                    position = line_start
                                    current_line_number = committed_line_number
                                    break
                                transcript_start_line = None
                                transcript_end_line = None
                                last_transcript_flush = time.time()
                            committed_position = handle.tell()
                            committed_line_number = next_line_number
                            position = committed_position
                            current_line_number = committed_line_number
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
                        committed_session_pos = session_pos
                        while True:
                            if stopper.is_set():
                                break
                            line_start = handle.tell()
                            line = handle.readline()
                            if not line:
                                session_pos = committed_session_pos
                                break
                            try:
                                entry = json.loads(line)
                            except Exception:
                                committed_session_pos = handle.tell()
                                session_pos = committed_session_pos
                                continue
                            if entry.get("type") != "message":
                                committed_session_pos = handle.tell()
                                session_pos = committed_session_pos
                                continue
                            msg = entry.get("message") or {}
                            role = msg.get("role")
                            if role not in {"user", "assistant"}:
                                committed_session_pos = handle.tell()
                                session_pos = committed_session_pos
                                continue
                            content = msg.get("content")
                            text = _extract_message_text(content).strip()
                            conversation_info = _extract_conversation_info(text)
                            if role == "user":
                                text = _extract_user_text(text)
                            text = text.replace("\n", " ").strip()
                            if not text:
                                committed_session_pos = handle.tell()
                                session_pos = committed_session_pos
                                continue
                            timestamp = entry.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%S")
                            if role == "user":
                                _maybe_reinforce(text, timestamp)
                            session_id = session_file.stem if session_file is not None else None
                            message_id = entry.get("id") or conversation_info.get("message_id")
                            conversation_id = conversation_info.get("conversation_id") or session_id
                            thread_id = conversation_info.get("thread_id") or session_id
                            transcript_line = f"{timestamp} [{role}] {text}"
                            retry_key = (str(session_file), line_start)
                            pending = pending_session_turns.get(retry_key)
                            if pending is None:
                                transcript_path, transcript_line_no = _append_transcript(transcript_target, timestamp, role, text)
                                turn_payload = {
                                    "role": role,
                                    "content": text,
                                    "conversation_id": conversation_id,
                                    "session_id": session_id,
                                    "thread_id": thread_id,
                                    "message_id": message_id,
                                    "source": "session",
                                    "timestamp": timestamp.replace("T", " ")[:19],
                                    "transcript_path": str(transcript_path),
                                    "transcript_offset": transcript_line_no,
                                    "transcript_end_offset": transcript_line_no,
                                    "metadata": {
                                        "parent_message_id": entry.get("parentId"),
                                    },
                                }
                                pending_session_turns[retry_key] = {
                                    "payload": dict(turn_payload),
                                    "transcript_line": transcript_line,
                                    "transcript_path": transcript_path,
                                    "transcript_line_no": transcript_line_no,
                                }
                            else:
                                turn_payload = dict(pending["payload"])
                                transcript_line = str(pending["transcript_line"])
                                transcript_path = Path(str(pending["transcript_path"]))
                                transcript_line_no = int(pending["transcript_line_no"])
                            if stopper.is_set():
                                break
                            if not _post_turn(turn_endpoint, turn_payload, stop_event=stopper):
                                session_pos = line_start
                                break
                            pending_session_turns.pop(retry_key, None)
                            recent_session_transcript_lines.append((str(transcript_path), transcript_line_no))
                            session_buffer.append(transcript_line)
                            session_last_path = transcript_path
                            session_last_timestamp = timestamp
                            if session_start_line is None:
                                session_start_line = transcript_line_no
                            session_end_line = transcript_line_no
                            if len(session_buffer) >= batch_max:
                                ok = _flush_buffer(
                                    session_buffer,
                                    source_label="session",
                                    transcript_path=session_last_path,
                                    timestamp=session_last_timestamp,
                                    start_line=session_start_line,
                                    end_line=session_end_line,
                                    stop_event=stopper,
                                )
                                if not ok:
                                    session_pos = line_start
                                    break
                                session_start_line = None
                                session_end_line = None
                                last_session_flush = time.time()
                            committed_session_pos = handle.tell()
                            session_pos = committed_session_pos
                except Exception:
                    pass

        now = time.time()
        if transcript_buffer and (now - last_transcript_flush) >= batch_seconds:
            ok = _flush_buffer(
                transcript_buffer,
                source_label=source,
                transcript_path=transcript_last_path,
                timestamp=transcript_last_timestamp,
                start_line=transcript_start_line,
                end_line=transcript_end_line,
                stop_event=stopper,
            )
            if ok:
                transcript_start_line = None
                transcript_end_line = None
                last_transcript_flush = now
        if session_buffer and (now - last_session_flush) >= batch_seconds:
            ok = _flush_buffer(
                session_buffer,
                source_label="session",
                transcript_path=session_last_path,
                timestamp=session_last_timestamp,
                start_line=session_start_line,
                end_line=session_end_line,
                stop_event=stopper,
            )
            if ok:
                session_start_line = None
                session_end_line = None
                last_session_flush = now

        poll_started = time.perf_counter()
        if stopper.wait(poll_seconds):
            if _SHUTDOWN_TRACE:
                print(
                    f"[ocmemog][watcher-poll] stop_wait timeout={poll_seconds:.3f}s elapsed={time.perf_counter()-poll_started:.3f}s",
                    file=sys.stderr,
                )
            return
    finally:
        _WATCHER_STOP_EVENT = None
        if _SHUTDOWN_TRACE:
            print("[ocmemog][watcher] shutdown loop exiting", file=sys.stderr)
        # no return value
