#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict
from urllib import error as urlerror
from urllib import request as urlrequest


def _post(base_url: str, path: str, payload: Dict[str, Any], *, token: str | None, timeout: float) -> Dict[str, Any]:
    req = urlrequest.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if token:
        req.add_header("x-ocmemog-token", token)
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _run_sequence(base_url: str, *, token: str | None, timeout: float, worker_id: int, iteration: int) -> Dict[str, Any]:
    session_id = f"soak-sess-{worker_id}"
    thread_id = f"soak-thread-{worker_id}"
    conversation_id = f"soak-conv-{worker_id}"
    user_id = f"u-{worker_id}-{iteration}"
    assistant_id = f"a-{worker_id}-{iteration}"
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")

    ingest_user = _post(
        base_url,
        "/conversation/ingest_turn",
        {
            "role": "user",
            "content": f"reliability soak iteration {iteration}: keep continuity hydrated",
            "conversation_id": conversation_id,
            "session_id": session_id,
            "thread_id": thread_id,
            "message_id": user_id,
            "timestamp": stamp,
        },
        token=token,
        timeout=timeout,
    )
    ingest_assistant = _post(
        base_url,
        "/conversation/ingest_turn",
        {
            "role": "assistant",
            "content": f"I will checkpoint and ponder iteration {iteration}.",
            "conversation_id": conversation_id,
            "session_id": session_id,
            "thread_id": thread_id,
            "message_id": assistant_id,
            "timestamp": stamp,
            "metadata": {"reply_to_message_id": user_id},
        },
        token=token,
        timeout=timeout,
    )
    hydrate = _post(
        base_url,
        "/conversation/hydrate",
        {
            "conversation_id": conversation_id,
            "session_id": session_id,
            "thread_id": thread_id,
            "turns_limit": 12,
            "memory_limit": 6,
        },
        token=token,
        timeout=timeout,
    )
    checkpoint = _post(
        base_url,
        "/conversation/checkpoint",
        {
            "conversation_id": conversation_id,
            "session_id": session_id,
            "thread_id": thread_id,
            "checkpoint_kind": "soak",
            "turns_limit": 12,
        },
        token=token,
        timeout=timeout,
    )
    checkpoint_expand = _post(
        base_url,
        "/conversation/checkpoint_expand",
        {
            "checkpoint_id": int((checkpoint.get("checkpoint") or {}).get("id") or 0),
            "turns_limit": 24,
            "radius_turns": 1,
        },
        token=token,
        timeout=timeout,
    )
    ponder = _post(
        base_url,
        "/memory/ponder",
        {"max_items": 4},
        token=token,
        timeout=timeout,
    )
    return {
        "ok": all(
            item.get("ok") is True
            for item in (ingest_user, ingest_assistant, hydrate, checkpoint, checkpoint_expand, ponder)
        ),
        "hydrateWarnings": hydrate.get("warnings", []),
        "checkpointId": (checkpoint.get("checkpoint") or {}).get("id"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a live reliability soak against the local ocmemog sidecar.")
    parser.add_argument("--base-url", default="http://127.0.0.1:17891")
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--token", default="")
    args = parser.parse_args()

    started = time.monotonic()
    failures = []
    successes = 0

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [
            pool.submit(
                _run_sequence,
                args.base_url,
                token=args.token or None,
                timeout=args.timeout,
                worker_id=(idx % max(1, args.workers)) + 1,
                iteration=idx + 1,
            )
            for idx in range(max(1, args.iterations))
        ]
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                failures.append({"error": str(exc)})
                continue
            if result.get("ok"):
                successes += 1
            else:
                failures.append(result)

    payload = {
        "ok": not failures,
        "iterations": max(1, args.iterations),
        "workers": max(1, args.workers),
        "successes": successes,
        "failures": failures,
        "elapsed_s": round(time.monotonic() - started, 3),
    }
    print(json.dumps(payload, indent=2))
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except urlerror.HTTPError as exc:
        print(json.dumps({"ok": False, "error": f"http {exc.code}", "detail": exc.reason}, indent=2))
        raise SystemExit(1)
    except urlerror.URLError as exc:
        print(json.dumps({"ok": False, "error": "unreachable", "detail": str(exc.reason)}, indent=2))
        raise SystemExit(1)
