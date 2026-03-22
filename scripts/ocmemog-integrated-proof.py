#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, IO, Optional
from urllib import error, request

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ENDPOINT = "http://127.0.0.1:17891"
DEFAULT_HOST = "127.0.0.1"
PYTHON_BIN = sys.executable


@dataclass
class SidecarSession:
    process: subprocess.Popen[bytes]
    endpoint: str
    log_path: Path
    log_handle: Optional[IO[bytes]] = None

    def stop(self) -> None:
        if self.process.poll() is not None:
            if self.log_handle is not None:
                self.log_handle.close()
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
        finally:
            if self.log_handle is not None:
                self.log_handle.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((DEFAULT_HOST, 0))
        return sock.getsockname()[1]


def _read_json(response: bytes) -> dict[str, Any]:
    try:
        return json.loads(response.decode("utf-8"))
    except Exception:
        return {}


def _post_json(endpoint: str, path: str, payload: dict[str, Any], *, timeout: int = 10) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(f"{endpoint.rstrip('/')}{path}", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return _read_json(body)


def _get_json(endpoint: str, path: str, *, timeout: int = 10) -> dict[str, Any]:
    req = request.Request(f"{endpoint.rstrip('/')}{path}", method="GET")
    with request.urlopen(req, timeout=timeout) as resp:
        return _read_json(resp.read())


def _wait_for_health(
    endpoint: str,
    *,
    timeout: int = 20,
    require_ready: bool = False,
    sidecar_process: subprocess.Popen[bytes] | None = None,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_error = ""
    last_payload: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            payload = _get_json(endpoint, "/healthz")
            if sidecar_process is not None and sidecar_process.poll() is not None:
                raise RuntimeError(
                    f"sidecar process exited during health check (code={sidecar_process.returncode})"
                )
            if isinstance(payload, dict):
                last_payload = payload
            if not isinstance(payload, dict) or not payload.get("ok"):
                last_error = "health not ok"
                time.sleep(0.2)
                continue
            if not require_ready or payload.get("ready") is not False:
                return payload
            last_error = f"health not ready (mode={payload.get('mode')})"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.2)
    if require_ready and last_payload:
        raise RuntimeError(f"health check did not reach ready: mode={last_payload.get('mode')} warnings={last_payload.get('warnings')}")
    if not last_payload:
        raise RuntimeError(f"health check failed: {last_error}")
    return last_payload


def _assert_contract_payload(payload: dict[str, Any], *, require_ready: bool = False) -> None:
    if not isinstance(payload, dict):
        raise RuntimeError("invalid health payload format")
    if not payload.get("ok"):
        raise RuntimeError("health check returned ok=false")
    if require_ready and payload.get("ready") is False:
        raise RuntimeError(f"health check not ready (mode={payload.get('mode')})")
    if payload.get("mode") == "degraded":
        return


def _validate_proof_payload(payload: dict[str, Any]) -> None:
    required = ("reference", "search_count", "linked_count")
    missing = [key for key in required if payload.get(key) in (None, "") and key not in payload]
    if missing:
        raise RuntimeError(f"proof payload missing required fields: {', '.join(missing)}")
    if not payload.get("ingest_ok"):
        raise RuntimeError("proof ingest contract failed")
    if not payload.get("search_ok"):
        raise RuntimeError("proof search contract failed")
    if not payload.get("get_ok"):
        raise RuntimeError("proof get contract failed")
    if not payload.get("hydrate_ok"):
        raise RuntimeError("proof hydrate contract failed")
    if int(payload.get("search_count") or 0) > 2:
        raise RuntimeError("proof search returned unbounded results")
    if int(payload.get("linked_count") or 0) > 2:
        raise RuntimeError("proof hydrate returned unbounded linked memories")


def _run_sidecar_probe(endpoint: str, *, require_ready: bool = False, timeout: int = 180) -> dict[str, Any]:
    _wait_for_health(endpoint, timeout=timeout, require_ready=require_ready)
    health = _get_json(endpoint, "/healthz")
    _assert_contract_payload(health, require_ready=require_ready)
    result = run_probe(endpoint)
    _validate_proof_payload(result)
    return result


def _run_sidecar_probe_with_process(
    endpoint: str,
    sidecar_process: subprocess.Popen[bytes],
    *,
    require_ready: bool = False,
    timeout: int = 180,
) -> dict[str, Any]:
    _wait_for_health(
        endpoint,
        timeout=timeout,
        require_ready=require_ready,
        sidecar_process=sidecar_process,
    )
    health = _get_json(endpoint, "/healthz")
    _assert_contract_payload(health, require_ready=require_ready)
    result = run_probe(endpoint)
    _validate_proof_payload(result)
    return result


def _start_sidecar(port: int, state_dir: Path) -> SidecarSession:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT_DIR) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["OCMEMOG_STATE_DIR"] = str(state_dir)
    env["OCMEMOG_TRANSCRIPT_WATCHER"] = "false"
    env["OCMEMOG_INGEST_ASYNC_WORKER"] = "true"
    env["OCMEMOG_AUTO_HYDRATION"] = "false"
    env["OCMEMOG_SEARCH_SKIP_EMBEDDING_PROVIDER"] = "true"
    endpoint = f"http://{DEFAULT_HOST}:{port}"
    env["OCMEMOG_HOST"] = DEFAULT_HOST
    env["OCMEMOG_PORT"] = str(port)

    log_path = state_dir / "sidecar-proof.log"
    log_handle = log_path.open("ab")
    proc = subprocess.Popen(
        [PYTHON_BIN, "-m", "uvicorn", "ocmemog.sidecar.app:app", "--host", DEFAULT_HOST, "--port", str(port)],
        cwd=str(ROOT_DIR),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    return SidecarSession(process=proc, endpoint=endpoint, log_path=log_path, log_handle=log_handle)


def _drain_ingest_queue(endpoint: str) -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        status = _get_json(endpoint, "/memory/ingest_status")
        if int(status.get("queueDepth", 0) or 0) <= 0:
            return
        time.sleep(0.25)
    flush = _post_json(endpoint, "/memory/ingest_flush", {"limit": 0}, timeout=20)
    if int(flush.get("queueDepth", 0) or 0) <= 0:
        return
    raise RuntimeError("ingest post-process queue did not drain within timeout")


def _derive_port(endpoint: str) -> int | None:
    if "://" not in endpoint:
        endpoint = f"{DEFAULT_HOST}:{endpoint}"
    parsed = endpoint.rsplit(":", 1)[-1]
    if parsed.isdigit():
        value = int(parsed)
        if value > 0:
            return value
    return None


def run_probe(endpoint: str) -> dict[str, Any]:
    token = "proof-token-demo-12345"
    conversation = "proof-conv-demo"
    session = "proof-sess-demo"
    thread = "proof-thread-demo"

    ingest_payload = {
        "content": f"I learned that the {token} is the canonical token for this verification.",
        "kind": "memory",
        "memory_type": "knowledge",
        "source": "ocmemog-proof",
        "conversation_id": conversation,
        "session_id": session,
        "thread_id": thread,
    }
    ingest_response = _post_json(endpoint, "/memory/ingest", ingest_payload, timeout=20)
    if not ingest_response.get("ok"):
        raise RuntimeError("/memory/ingest did not return ok")
    reference = str(ingest_response.get("reference") or "")
    if not reference:
        raise RuntimeError("/memory/ingest response missing reference")

    # /memory/ingest is synchronous for the core write; post-process queue may settle later.
    search_response = _post_json(endpoint, "/memory/search", {"query": token, "limit": 2}, timeout=20)
    if not search_response.get("ok"):
        raise RuntimeError("/memory/search did not return ok")
    results = list(search_response.get("results") or [])
    if len(results) > 2:
        raise RuntimeError("/memory/search exceeded limit and did not compact results")
    if not results:
        raise RuntimeError("/memory/search returned no results for a distinctive memory")
    matched_reference = str(results[0].get("reference") or "")
    if reference and reference != matched_reference:
        # allow reordering when other memories are present, but require recalled memory present
        if not any(str(item.get("reference") or "") == reference for item in results):
            raise RuntimeError("/memory/search did not return the ingested memory reference")

    get_response = _post_json(endpoint, "/memory/get", {"reference": reference}, timeout=20)
    if not get_response.get("ok"):
        raise RuntimeError("/memory/get for ingested reference failed")
    if token not in str(get_response.get("memory", {}).get("content") or ""):
        raise RuntimeError("/memory/get content did not match ingested distinctive token")

    hydrate_response = _post_json(
        endpoint,
        "/conversation/hydrate",
        {
            "conversation_id": conversation,
            "session_id": session,
            "thread_id": thread,
            "turns_limit": 2,
            "memory_limit": 2,
        },
        timeout=20,
    )
    if not hydrate_response.get("ok"):
        raise RuntimeError("/conversation/hydrate failed")
    linked_memories = hydrate_response.get("linked_memories") or []
    if len(linked_memories) > 2:
        raise RuntimeError("/conversation/hydrate did not compact linked memories")

    return {
        "reference": reference,
        "ingest_ok": True,
        "search_ok": True,
        "search_count": len(results),
        "get_ok": True,
        "hydrate_ok": True,
        "linked_count": len(linked_memories),
        "endpoint": endpoint,
    }


def run_legacy_probe(endpoint: str) -> dict[str, Any]:
    # Legacy check keeps state untouched and verifies a focused path read against existing sidecar.
    probe_token = f"legacy-{uuid.uuid4().hex}"
    payload = {
        "content": f"Legacy probe content: {probe_token}",
        "kind": "memory",
        "memory_type": "knowledge",
    }
    ingest = _post_json(endpoint, "/memory/ingest", payload, timeout=20)
    if not ingest.get("ok"):
        raise RuntimeError("legacy /memory/ingest failed")
    search = _post_json(endpoint, "/memory/search", {"query": probe_token, "limit": 1}, timeout=20)
    results = list(search.get("results") or [])
    if not results:
        raise RuntimeError("legacy /memory/search did not return ingested probe")
    return {
        "endpoint": endpoint,
        "token": probe_token,
        "legacy_ok": True,
        "search_count": len(results),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--start-sidecar", action="store_true")
    parser.add_argument("--legacy-endpoint", default="")
    parser.add_argument("--state-dir", default="")
    args = parser.parse_args()

    start = bool(args.start_sidecar)
    endpoint = args.endpoint.rstrip("/")
    state_dir: Path | None = Path(args.state_dir) if args.state_dir else None
    if start:
        if state_dir is None:
            state_dir_ctx = tempfile.TemporaryDirectory(prefix="ocmemog-proof-")
            state_dir = Path(state_dir_ctx.name)
        else:
            state_dir_ctx = None
            state_dir.mkdir(parents=True, exist_ok=True)

        port = _derive_port(endpoint) or _free_port()
        session = None
        try:
            session = _start_sidecar(port, state_dir)
            result = _run_sidecar_probe_with_process(
                session.endpoint,
                session.process,
                require_ready=False,
                timeout=args.timeout,
            )
            endpoint = session.endpoint
            if args.legacy_endpoint:
                legacy = run_legacy_probe(args.legacy_endpoint.rstrip("/"))
                result.update({"legacy": legacy})
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        except Exception as exc:
            print(f"ERROR: contract proof failed to start or probe sidecar: {exc}", file=sys.stderr)
            return 1
        finally:
            if session is not None:
                session.stop()
            if state_dir_ctx is not None:
                state_dir_ctx.cleanup()
    else:
        try:
            result = _run_sidecar_probe(endpoint, require_ready=False, timeout=args.timeout)
            if args.legacy_endpoint:
                result.update({"legacy": run_legacy_probe(args.legacy_endpoint.rstrip("/"))})
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        except error.URLError as exc:
            print(f"ERROR: sidecar not reachable at {endpoint}: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"ERROR: contract proof failed: {exc}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
