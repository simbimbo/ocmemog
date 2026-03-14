# ocmemog Memory Usage

## Current operating model

ocmemog is a repo-local OpenClaw memory sidecar backed by SQLite. It is not a full brAIn runtime clone. The safe assumption is:

- search/get over local memory are supported
- heuristic embeddings are supported by default
- several advanced brAIn memory flows are copied in but still degraded by missing runtime dependencies

## Running the sidecar

Use the provided launcher:

```bash
scripts/ocmemog-sidecar.sh
```

## Transcript watcher (auto-ingest)

Manual watcher:

```bash
# defaults to ~/.openclaw/workspace/memory/transcripts if not set
export OCMEMOG_TRANSCRIPT_DIR="$HOME/.openclaw/workspace/memory/transcripts"
export OCMEMOG_INGEST_ENDPOINT="http://127.0.0.1:17890/memory/ingest"
./scripts/ocmemog-transcript-watcher.sh
```

Auto-start inside sidecar:

```bash
export OCMEMOG_TRANSCRIPT_WATCHER=true
./scripts/ocmemog-sidecar.sh
```

Useful environment variables:

```bash
export OCMEMOG_HOST=127.0.0.1
export OCMEMOG_PORT=17890
export OCMEMOG_STATE_DIR=/path/to/state
export OCMEMOG_DB_PATH=/path/to/brain_memory.sqlite3
export OCMEMOG_MEMORY_MODEL=gpt-4o-mini
export OCMEMOG_OPENAI_API_KEY=sk-...
export OCMEMOG_OPENAI_API_BASE=https://api.openai.com/v1
export OCMEMOG_OPENAI_EMBED_MODEL=text-embedding-3-small
export BRAIN_EMBED_MODEL_LOCAL=simple
export BRAIN_EMBED_MODEL_PROVIDER=openai
export OCMEMOG_TRANSCRIPT_DIR=$HOME/.openclaw/workspace/memory/transcripts
export OCMEMOG_TRANSCRIPT_GLOB=*.log
export OCMEMOG_TRANSCRIPT_POLL_SECONDS=1
export OCMEMOG_INGEST_KIND=memory
export OCMEMOG_INGEST_SOURCE=transcript
export OCMEMOG_TRANSCRIPT_WATCHER=true
```

Default state location in this repo is `.ocmemog-state/`.

## Plugin API

Health:

```bash
curl http://127.0.0.1:17890/healthz
```

Search:

```bash
curl -s http://127.0.0.1:17890/memory/search \
  -H 'content-type: application/json' \
  -d '{"query":"deploy risk","limit":5,"categories":["knowledge","tasks"]}'
```

Get by reference:

```bash
curl -s http://127.0.0.1:17890/memory/get \
  -H 'content-type: application/json' \
  -d '{"reference":"knowledge:12"}'
```

Ingest content:

```bash
curl -s http://127.0.0.1:17890/memory/ingest \
  -H 'content-type: application/json' \
  -d '{"content":"remember this","kind":"memory","memory_type":"knowledge"}'
```

Distill recent experiences:

```bash
curl -s http://127.0.0.1:17890/memory/distill \
  -H 'content-type: application/json' \
  -d '{"limit":10}'
```

Notes:

- Valid sidecar categories today are `knowledge`, `reflections`, `directives`, `tasks`, `runbooks`, and `lessons`.
- `/memory/get` currently expects a `table:id` reference.
- Runtime degradation is reported in every sidecar response.

## What is safe to rely on

- `store.init_db()` creates the local schema automatically
- `retrieval.retrieve_for_queries()` is the main sidecar search path
- `vector_index.search_memory()` provides a semantic fallback over `knowledge`, `runbooks`, `lessons`, `directives`, `reflections`, and `tasks` when keyword retrieval misses
- `probe_runtime()` exposes missing shim replacements and optional embedding warnings

## What is not safe to rely on yet

- `brain/runtime/memory/api.py`
  - It targets missing/legacy tables and columns.
- Provider-backed embeddings
  - Available when `BRAIN_EMBED_MODEL_PROVIDER=openai` and `OCMEMOG_OPENAI_API_KEY` is set.
  - Falls back to local embeddings when missing.
- Model-backed distillation
  - Available when `OCMEMOG_OPENAI_API_KEY` is set; otherwise falls back to heuristic distill.
- Role-prioritized context building
  - `brain.runtime.roles.role_registry` is not bundled here.
- Full brAIn memory parity
  - The repo ships only a subset of the original runtime architecture.

## Suggested local workflows

To inspect memory state quickly:

```bash
python3 - <<'PY'
from brain.runtime.memory import store
store.init_db()
conn = store.connect()
for table in ("knowledge", "reflections", "directives", "tasks", "candidates", "promotions"):
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(table, count)
conn.close()
PY
```

To rebuild embeddings for recent knowledge rows:

```bash
python3 - <<'PY'
from brain.runtime.memory import vector_index
print(vector_index.index_memory())
PY
```

## TODO: Missing runtime dependencies

- TODO: wire a real inference backend before enabling distill/promote as an operator-facing workflow
- TODO: wire real provider execution if `BRAIN_EMBED_MODEL_PROVIDER` is meant to do anything
- TODO: add or remove role-based context selection; the current import path is absent
- TODO: harden `/memory/get` with a table allow-list before exposing the sidecar outside trusted local use
- TODO: decide whether to expose `runbooks` and `lessons` in the plugin API
