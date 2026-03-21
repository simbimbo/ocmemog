#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Iterable
from urllib import request as urlrequest

DEFAULT_ENDPOINT = "http://127.0.0.1:17891"
DEFAULT_EXTS = {".md", ".txt", ".log", ".jsonl"}
DEFAULT_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "dist",
    "build",
    "__pycache__",
    ".DS_Store",
    "Library",
    ".Trash",
    ".cache",
    ".openclaw/logs",
}

ASYNC_DEFAULT = os.environ.get("OCMEMOG_INGEST_ASYNC_DEFAULT", "true").lower() in {"1", "true", "yes"}
INGEST_PATH = "/memory/ingest_async" if ASYNC_DEFAULT else "/memory/ingest"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _post_json(endpoint: str, path: str, payload: dict, *, timeout: int = 20) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(endpoint.rstrip("/") + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    try:
        return json.loads(body)
    except Exception:
        return {"ok": False, "raw": body}


def _chunk_text(text: str, max_len: int = 800) -> list[str]:
    chunks: list[str] = []
    buf: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("```"):
            continue
        if not line.strip():
            if buf:
                chunk = " ".join(buf).strip()
                if len(chunk) >= 40:
                    chunks.append(chunk)
                buf = []
            continue
        buf.append(line.strip())
    if buf:
        chunk = " ".join(buf).strip()
        if len(chunk) >= 40:
            chunks.append(chunk)
    trimmed: list[str] = []
    for c in chunks:
        if len(c) > max_len:
            mid = len(c) // 2
            trimmed.append(c[:mid].strip())
            trimmed.append(c[mid:].strip())
        else:
            trimmed.append(c)
    return [c for c in trimmed if c]


def _classify_bucket(text: str) -> str:
    head = text.lower()
    if "runbook" in head or "procedure" in head or "steps" in head:
        return "runbooks"
    if "lesson" in head or "postmortem" in head or "learned" in head:
        return "lessons"
    if "todo" in head or "next steps" in head or "task" in head:
        return "tasks"
    if "directive" in head or "rule" in head:
        return "directives"
    if "reflection" in head:
        return "reflections"
    return "knowledge"


def _walk_sources(roots: Iterable[Path], exts: set[str], max_files: int, max_size_kb: int) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            rel = os.path.relpath(dirpath, root)
            dirnames[:] = [d for d in dirnames if d not in DEFAULT_SKIP_DIRS]
            if any(part in DEFAULT_SKIP_DIRS for part in rel.split(os.sep)):
                continue
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix.lower() not in exts:
                    continue
                try:
                    size_kb = path.stat().st_size / 1024
                except Exception:
                    continue
                if size_kb > max_size_kb:
                    continue
                files.append(path)
                if len(files) >= max_files:
                    return files
    return files


def _sample_query(text: str) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{3,}", text)
    if not words:
        return "memory"
    pick = random.sample(words, k=min(3, len(words)))
    return " ".join(pick)


def _choose_threshold(confidences: list[float], reject_fraction: float) -> float | None:
    if not confidences:
        return None
    ordered = sorted(confidences)
    idx = max(0, min(len(ordered) - 1, int((1.0 - reject_fraction) * len(ordered))))
    return float(ordered[idx])


def _distill_batches(endpoint: str, target: int, batch_sizes: list[int], timeout: int, budget_s: int) -> dict:
    attempts = []
    total = 0
    start = time.time()
    for batch in batch_sizes:
        if time.time() - start > budget_s:
            break
        try:
            resp = _post_json(endpoint, "/memory/distill", {"limit": batch}, timeout=timeout)
        except Exception as exc:
            resp = {"ok": False, "error": str(exc)}
        count = resp.get("count") if isinstance(resp, dict) else None
        if isinstance(count, int):
            total += count
        attempts.append({"batch": batch, "ok": resp.get("ok"), "count": count, "error": resp.get("error")})
        if total >= target:
            break
    return {"attempts": attempts, "total": total, "elapsed_s": round(time.time() - start, 3)}


def _enable_local_embeddings() -> None:
    os.environ.setdefault("OCMEMOG_EMBED_MODEL_LOCAL", "")
    os.environ.setdefault("OCMEMOG_EMBED_MODEL_PROVIDER", "local-openai")
    os.environ.setdefault("BRAIN_EMBED_MODEL_LOCAL", os.environ["OCMEMOG_EMBED_MODEL_LOCAL"])
    os.environ.setdefault("BRAIN_EMBED_MODEL_PROVIDER", os.environ["OCMEMOG_EMBED_MODEL_PROVIDER"])
    os.environ.setdefault("OCMEMOG_LOCAL_EMBED_BASE_URL", "http://127.0.0.1:18081/v1")
    os.environ.setdefault("OCMEMOG_LOCAL_EMBED_MODEL", "nomic-embed-text-v1.5")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--max-files", type=int, default=600)
    parser.add_argument("--max-chunks", type=int, default=1200)
    parser.add_argument("--max-size-kb", type=int, default=512)
    parser.add_argument("--experience-count", type=int, default=250)
    parser.add_argument("--distill-target", type=int, default=60)
    parser.add_argument("--distill-batches", default="10")
    parser.add_argument("--distill-timeout", type=int, default=45)
    parser.add_argument("--distill-budget", type=int, default=120)
    parser.add_argument("--query-samples", type=int, default=40)
    parser.add_argument("--promote-limit", type=int, default=60)
    parser.add_argument("--promotion-reject-rate", type=float, default=0.1)
    parser.add_argument("--promotion-shadow", action="store_true")
    parser.add_argument("--demote-limit", type=int, default=20)
    parser.add_argument("--demote-threshold", type=float, default=0.2)
    parser.add_argument("--demote-force", action="store_true")
    parser.add_argument("--ponder-limit", type=int, default=5)
    parser.add_argument("--report", default=str(REPO_ROOT / "reports" / "test-rig-latest.json"))
    args = parser.parse_args()

    _enable_local_embeddings()

    default_workspace = Path.home() / ".openclaw" / "workspace"
    roots = [default_workspace]
    env_roots = os.environ.get("OCMEMOG_TEST_RIG_ROOTS", "").strip()
    if env_roots:
        roots = [Path(p).expanduser() for p in env_roots.split(os.pathsep) if p.strip()]

    files = _walk_sources(roots, DEFAULT_EXTS, args.max_files, args.max_size_kb)

    chunks: list[dict] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for chunk in _chunk_text(text):
            chunks.append({"source": str(path), "content": chunk})
            if len(chunks) >= args.max_chunks:
                break
        if len(chunks) >= args.max_chunks:
            break

    random.shuffle(chunks)

    ingest_start = time.time()
    mem_count = 0
    for entry in chunks:
        bucket = _classify_bucket(entry["content"])
        source_path = str(entry["source"])
        if "/memory/" in source_path or "/memory/transcripts/" in source_path:
            bucket = "reflections"
        payload = {
            "content": entry["content"],
            "kind": "memory",
            "memory_type": bucket,
            "source": f"rig:{entry['source']}",
            "session_id": f"rig-session:{Path(entry['source']).name}",
            "thread_id": f"rig-thread:{Path(entry['source']).stem}",
            "message_id": None,
            "transcript_path": entry["source"],
            "transcript_offset": None,
        }
        _post_json(args.endpoint, INGEST_PATH, payload)
        mem_count += 1
    ingest_elapsed = time.time() - ingest_start

    exp_count = 0
    for entry in chunks[: args.experience_count]:
        payload = {
            "content": entry["content"],
            "kind": "experience",
            "source": f"rig:{entry['source']}",
        }
        _post_json(args.endpoint, INGEST_PATH, payload)
        exp_count += 1

    distill_result = _distill_batches(
        args.endpoint,
        args.distill_target,
        [int(x) for x in args.distill_batches.split(",") if x.strip().isdigit()],
        args.distill_timeout,
        args.distill_budget,
    )

    # query sampling (search)
    query_samples = random.sample(chunks, k=min(args.query_samples, len(chunks))) if chunks else []
    query_results = []
    for entry in query_samples:
        query = _sample_query(entry["content"])
        t0 = time.time()
        resp = _post_json(args.endpoint, "/memory/search", {"query": query, "limit": 5})
        elapsed = time.time() - t0
        hits = 0
        for item in resp.get("results", []) or []:
            content = str(item.get("content") or "")
            if any(token.lower() in content.lower() for token in query.split()):
                hits += 1
        query_results.append({"query": query, "elapsed": elapsed, "hits": hits})

    # local pipeline: promote + ponder + research synthesis
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    promote_summary = {}
    demote_summary = {}
    ponder_summary = {}
    research_summary = {}
    try:
        from ocmemog.runtime.memory import promote, store, pondering_engine, memory_synthesis, semantic_search

        conn = store.connect()
        rows = conn.execute(
            "SELECT candidate_id, confidence_score FROM candidates WHERE status='pending' ORDER BY created_at DESC LIMIT ?",
            (args.promote_limit,),
        ).fetchall()
        conn.close()
        confidences = [float(r[1] or 0.0) for r in rows]
        threshold = _choose_threshold(confidences, args.promotion_reject_rate)
        shadow_rejects = sum(1 for c in confidences if threshold is not None and c < threshold)
        if not args.promotion_shadow and threshold is not None:
            promote.config.OCMEMOG_PROMOTION_THRESHOLD = threshold

        promoted = 0
        rejected = 0
        for row in rows:
            cid = row[0]
            res = promote.promote_candidate_by_id(cid)
            if res.get("decision") == "promote":
                promoted += 1
            elif res.get("decision") == "reject":
                rejected += 1
        promote_summary = {
            "attempted": len(rows),
            "promoted": promoted,
            "rejected": rejected,
            "shadow_threshold": threshold,
            "shadow_rejects": shadow_rejects,
            "shadow_mode": bool(args.promotion_shadow),
        }

        demote = promote.demote_by_confidence(limit=args.demote_limit, threshold=args.demote_threshold, force=args.demote_force)
        demote_summary = {"count": demote.get("count"), "threshold": demote.get("threshold")}

        ponder = pondering_engine.run_ponder_cycle(max_items=args.ponder_limit)
        ponder_summary = {
            "unresolved": len(ponder.get("unresolved", []) or []),
            "insights": len(ponder.get("insights", []) or []),
            "links": len(ponder.get("links", []) or []),
        }

        synth = memory_synthesis.synthesize_memory_patterns(limit=5)
        semantic_queries = [q["query"] for q in query_results[:5]]
        semantic = []
        for q in semantic_queries:
            semantic.append({"query": q, "results": semantic_search.semantic_search(q, limit=3)})
        research_summary = {"synthesis": synth, "semantic": semantic}
    except Exception as exc:
        promote_summary = {"error": str(exc)}
        demote_summary = {"error": str(exc)}

    report = {
        "files_scanned": len(files),
        "chunks_ingested": mem_count,
        "experiences_ingested": exp_count,
        "ingest_elapsed_s": round(ingest_elapsed, 3),
        "distill": distill_result,
        "query_samples": query_results,
        "query_hit_rate": round(sum(1 for q in query_results if q["hits"] > 0) / max(1, len(query_results)), 3),
        "promote": promote_summary,
        "demote": demote_summary,
        "ponder": ponder_summary,
        "research": research_summary,
    }

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2))

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
