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

Useful environment variables:

```bash
export OCMEMOG_HOST=127.0.0.1
export OCMEMOG_PORT=17890
export OCMEMOG_STATE_DIR=/path/to/state
export OCMEMOG_DB_PATH=/path/to/brain_memory.sqlite3
export BRAIN_EMBED_MODEL_LOCAL=simple
export BRAIN_EMBED_MODEL_PROVIDER=
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

Notes:

- Valid sidecar categories today are `knowledge`, `reflections`, `directives`, `tasks`, `runbooks`, and `lessons`.
- `/memory/get` currently expects a `table:id` reference.
- Runtime degradation is reported in every sidecar response.

## What is safe to rely on

- `store.init_db()` creates the local schema automatically
- `retrieval.retrieve_for_queries()` is the main sidecar search path
- `vector_index.search_memory()` provides a semantic fallback when keyword retrieval misses knowledge
- `probe_runtime()` exposes missing shim replacements and optional embedding warnings

## What is not safe to rely on yet

- `brain/runtime/memory/api.py`
  - It targets missing/legacy tables and columns.
- Provider-backed embeddings
  - `brain.runtime.providers` and `brain.runtime.model_router` are still shims.
- Model-backed distillation
  - `brain.runtime.inference.infer()` is a hard failure stub.
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
