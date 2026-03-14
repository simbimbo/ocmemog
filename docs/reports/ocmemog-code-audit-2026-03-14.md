# ocmemog deep audit (2026-03-14)

**Scope:** full plugin repo, sidecar, memory runtime, scripts. Goal: public‑facing quality with minimal surprises.

---

## 1) TypeScript plugin layer

### `openclaw.plugin.json`
- ✅ `id: memory-ocmemog`, `kind: memory`.
- ✅ Config schema includes `endpoint`, `timeoutMs`.
- **Callout risk:** none.

### `index.ts`
- ✅ Uses OpenClaw plugin scaffold; forwards memory tools to sidecar.
- **Callout risk:** if sidecar is down, tool calls should return clear error (verify by manual test).

### `package.json`
- ✅ Public package metadata, MIT license, `files` list, `publishConfig.access=public`.
- ✅ `openclaw.extensions` points to `index.ts` (JITI loads TS at runtime).
- **Callout risk:** repository URL must exist when publishing.

---

## 2) Sidecar API (FastAPI)

### `ocmemog/sidecar/app.py`

**Endpoints:**
- `/memory/search`, `/memory/get`, `/memory/ingest`, `/memory/distill`, `/memory/context`, `/memory/ponder`, `/metrics`, `/events`, `/dashboard`, `/healthz`.

**Security / robustness:**
- ✅ Added optional auth: `OCMEMOG_API_TOKEN` (header `x-ocmemog-token` or `Authorization: Bearer`).
- ✅ Transcript context retrieval now **allow‑listed** by `OCMEMOG_TRANSCRIPT_ROOTS`.
- ✅ Transcript snippet reading **streams** only needed lines.
- ✅ Loopback bind still default in sidecar launcher.

**Potential callouts:**
- If user binds to `0.0.0.0` without token, sidecar is exposed.
- `/events` is an open SSE stream; if exposed, may leak memory logs.

---

## 3) Compatibility / runtime shims

### `ocmemog/sidecar/compat.py`
- ✅ Detects missing deps; reports warnings instead of crashing.
- **Callout risk:** warnings are expected in “degraded” mode.

---

## 4) Memory store & schema

### `brain/runtime/memory/store.py`
- ✅ SQLite schema created on demand.
- ✅ Added `demotions` and `cold_storage` tables (archive demoted memories).

**Callout risk:**
- Cold storage grows unbounded (acceptable for “disk cheap” stance).

---

## 5) Memory pipelines

### `brain/runtime/memory/retrieval.py`
- ✅ Simple keyword match + semantic fallback.
- **Callout risk:** keyword matching is naive; might be criticized for low recall. (Mitigate by embeddings + vector index.)

### `brain/runtime/memory/vector_index.py`
- ✅ Embedding index stored in SQLite; semantic search loads all embeddings in memory.
- **Callout risk:** O(N) scan can be slow at scale. Acceptable for local use; note in docs.

### `brain/runtime/memory/distill.py`
- ✅ Uses Ollama inference if configured; heuristic fallback.
- **Callout risk:** distill quality depends on local model; acceptable for “best effort”.

### `brain/runtime/memory/promote.py`
- ✅ Promotion threshold configurable (`OCMEMOG_PROMOTION_THRESHOLD`).
- ✅ Demotion archives into `cold_storage` and removes from hot tables.
- **Callout risk:** Demotion is destructive to hot table (archived safe).

### `brain/runtime/memory/context_builder.py`
- ✅ Uses retrieval for context blocks.
- **Callout risk:** role registry missing in this repo (already noted in compat warnings).

### `brain/runtime/memory/pondering_engine.py`
- ✅ Pondering now exposed via API and writes summaries into `reflections`.

---

## 6) Embeddings & inference

### `brain/runtime/embedding_engine.py`
- ✅ Local embeddings supported (simple/hash or sentence-transformers).
- ✅ Provider embeddings via OpenAI or Ollama.
- **Callout risk:** if `BRAIN_EMBED_MODEL_LOCAL` is set, it overrides provider embeddings.

### `brain/runtime/providers.py`
- ✅ OpenAI and Ollama embedding calls.
- ✅ Added structured error logging to `brain_memory.log.jsonl`.

### `brain/runtime/inference.py`
- ✅ Ollama inference supported; OpenAI fallback.
- ✅ Added structured error logging.

---

## 7) Sidecar scripts

### `scripts/ocmemog-sidecar.sh`
- ✅ Defaults to Ollama (phi3 + nomic‑embed‑text).
- ✅ Configurable thresholds + token.

### `scripts/ocmemog-test-rig.py`
- ✅ Stress rig: ingest, distill, promote, demote, ponder, research.
- ✅ Supports shadow promotion mode (avoid rejecting useful data).

### `scripts/ocmemog-demo.py`
- ✅ Presentation‑friendly demo with `--pretty`.

---

## 8) Known TODOs / warnings
- Sidecar reports missing role registry for role‑based prioritization.
- Optional dependency: `sentence-transformers` (only if you want local non‑Ollama embeddings).
- Vector index is naive (O(N)).

---

## 9) Dependencies

**Python**
- `fastapi`
- `uvicorn[standard]`

**System**
- `ollama`

---

## 10) Recommendations before public launch

1) **Add a short SECURITY note** to README:
   - sidecar is local by default
   - if bound to 0.0.0.0, require `OCMEMOG_API_TOKEN`

2) **Add scale warning** for vector search (O(N))

3) **Tag release** `v0.1.0` and publish npm package.

---

## 11) Summary

The codebase is publish‑ready after the security hardening and doc updates above. Remaining risks are acceptable given local‑first design. No critical missing dependencies or crashes detected.
