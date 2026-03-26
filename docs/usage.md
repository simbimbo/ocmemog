# ocmemog Memory Usage

## Current operating model

ocmemog is a repo-local OpenClaw memory sidecar backed by SQLite with llama.cpp-first local inference and embeddings. It is not a full brAIn runtime clone. The safe assumption is:

- search/get over local memory are supported
- provider-backed local embeddings are the primary path
- some advanced memory flows still depend on compatibility-shimmed runtime surfaces and may run in degraded mode depending on the configured provider/runtime

## Running the sidecar

Use the provided launcher:

```bash
scripts/ocmemog-sidecar.sh
```

## Transcript watcher (auto-ingest)

Manual watcher:

```bash
# if you set OCMEMOG_TRANSCRIPT_DIR, that path is watched; otherwise defaults to
# ~/.openclaw/workspace/memory/transcripts (or ~/.openclaw/agents/main/sessions when
# OCMEMOG_SESSION_DIR is set and no transcript path is given)
export OCMEMOG_TRANSCRIPT_DIR="$HOME/.openclaw/workspace/memory/transcripts"
export OCMEMOG_SESSION_DIR="$HOME/.openclaw/agents/main/sessions"
export OCMEMOG_INGEST_ENDPOINT="http://127.0.0.1:17891/memory/ingest_async"
export OCMEMOG_TRANSCRIPT_POLL_SECONDS=30
export OCMEMOG_INGEST_BATCH_SECONDS=30
export OCMEMOG_INGEST_BATCH_MAX=25
./scripts/ocmemog-transcript-watcher.sh
```

Auto-start inside sidecar:

```bash
export OCMEMOG_TRANSCRIPT_WATCHER=true
./scripts/ocmemog-sidecar.sh
```

On macOS laptops, the launcher defaults to `OCMEMOG_LAPTOP_MODE=auto`, which detects battery power and uses lower-impact watcher settings automatically. Override with `OCMEMOG_LAPTOP_MODE=ac` for wall-power behavior or `OCMEMOG_LAPTOP_MODE=battery` to force conservative mode.

Useful environment variables:

```bash
export OCMEMOG_HOST=127.0.0.1
export OCMEMOG_PORT=17891
export OCMEMOG_STATE_DIR=/path/to/state
export OCMEMOG_DB_PATH=/path/to/ocmemog_memory.sqlite3
export OCMEMOG_MEMORY_MODEL=gpt-4o-mini
export OCMEMOG_OPENAI_API_KEY=sk-...
export OCMEMOG_OPENAI_API_BASE=https://api.openai.com/v1
export OCMEMOG_OPENAI_EMBED_MODEL=text-embedding-3-small
export OCMEMOG_LOCAL_LLM_BASE_URL=http://127.0.0.1:18080/v1
export OCMEMOG_LOCAL_LLM_MODEL=qwen2.5-7b-instruct
export OCMEMOG_LOCAL_EMBED_BASE_URL=http://127.0.0.1:18081/v1
export OCMEMOG_LOCAL_EMBED_MODEL=nomic-embed-text-v1.5
export OCMEMOG_EMBED_MODEL_LOCAL=simple
export OCMEMOG_EMBED_MODEL_PROVIDER=local-openai
export OCMEMOG_SESSION_DIR=$HOME/.openclaw/agents/main/sessions
export OCMEMOG_TRANSCRIPT_DIR=$HOME/.openclaw/workspace/memory/transcripts
export OCMEMOG_TRANSCRIPT_GLOB=*.log
export OCMEMOG_TRANSCRIPT_WATCHER=true
export OCMEMOG_TRANSCRIPT_POLL_SECONDS=30
export OCMEMOG_INGEST_KIND=memory
export OCMEMOG_INGEST_SOURCE=transcript
export OCMEMOG_INGEST_ASYNC_WORKER=true
export OCMEMOG_INGEST_ASYNC_POLL_SECONDS=5
export OCMEMOG_INGEST_ASYNC_BATCH_MAX=25
export OCMEMOG_INGEST_BATCH_SECONDS=30
export OCMEMOG_INGEST_BATCH_MAX=25
export OCMEMOG_SHUTDOWN_DRAIN_QUEUE=false
export OCMEMOG_WORKER_SHUTDOWN_TIMEOUT_SECONDS=0.35
export OCMEMOG_SHUTDOWN_TIMING=true
export OCMEMOG_SHUTDOWN_DUMP_THREADS=false
```

Default state location in this repo is `.ocmemog-state/`.

On shutdown, set `OCMEMOG_SHUTDOWN_DRAIN_QUEUE=true` to synchronously flush queued ingest entries before exit. This is useful for short-running deployments and tests that expect strong delivery guarantees.

Queue behavior notes:
- malformed queue lines are now treated as durable queue errors and skipped/acknowledged so a single bad payload does not block later valid work
- valid payload failures are retried with a bounded in-queue retry marker (`_ocmemog_retry_count`) instead of blocking forever on the first poison item
- `OCMEMOG_INGEST_MAX_RETRIES` controls how many failed attempts a queued payload gets before it is dropped and recorded as a retry-exhausted error
- runtime queue stats keep the last queue parse/retry error visible via `QUEUE_STATS["last_error"]`
- `ocmemog-doctor` queue health now distinguishes invalid queue lines from retrying payloads so operators can tell parsing damage apart from poison-item retries

## Plugin API

Health:

```bash
curl http://127.0.0.1:17891/healthz
```

Realtime metrics + events:

```bash
curl http://127.0.0.1:17891/metrics
curl http://127.0.0.1:17891/events
```

Dashboard:

```bash
open http://127.0.0.1:17891/dashboard
```

Search:

```bash
curl -s http://127.0.0.1:17891/memory/search \
  -H 'content-type: application/json' \
  -d '{"query":"deploy risk","limit":5,"categories":["knowledge","tasks"]}'
```

If `OCMEMOG_API_TOKEN` is set, include the header:

```bash
-H 'x-ocmemog-token: YOUR_TOKEN'
```

Get by reference:

```bash
curl -s http://127.0.0.1:17891/memory/get \
  -H 'content-type: application/json' \
  -d '{"reference":"knowledge:12"}'
```

Fetch linked context (transcript snippet):

```bash
curl -s http://127.0.0.1:17891/memory/context \
  -H 'content-type: application/json' \
  -d '{"reference":"knowledge:12","radius":10}'
```

Helper script:

```bash
./scripts/ocmemog-context.sh knowledge:12 10
```

Run pondering (writes summaries into reflections):

```bash
curl -s http://127.0.0.1:17891/memory/ponder \
  -H 'content-type: application/json' \
  -d '{"max_items":5}'
```

Fetch latest ponder recommendations:

```bash
curl -s http://127.0.0.1:17891/memory/ponder/latest?limit=5
```

Ingest content:

```bash
curl -s http://127.0.0.1:17891/memory/ingest \
  -H 'content-type: application/json' \
  -d '{"content":"remember this","kind":"memory","memory_type":"knowledge"}'
```

Ingest with context anchors (links to chat/transcript):

```bash
curl -s http://127.0.0.1:17891/memory/ingest \
  -H 'content-type: application/json' \
  -d '{
        "content":"remember this",
        "kind":"memory",
        "memory_type":"knowledge",
        "session_id":"session-123",
        "thread_id":"thread-abc",
        "message_id":"msg-987",
        "transcript_path":"/path/to/transcript.log",
        "transcript_offset":420,
        "timestamp":"2026-02-22 16:04:52"
      }'
```

Distill recent experiences:

```bash
curl -s http://127.0.0.1:17891/memory/distill \
  -H 'content-type: application/json' \
  -d '{"limit":10}'
```

Notes:

- Valid sidecar categories today are `knowledge`, `reflections`, `directives`, `tasks`, `runbooks`, and `lessons`.
- `/memory/get` currently expects a `table:id` reference.
- Runtime degradation is reported in every sidecar response.
- Sidecar responses now also include `runtimeSummary`, a compact operator-facing summary of runtime mode, embedding provider, local embedding model, embedding path readiness/fallback state, queue health snapshot, shim surface count, and missing dependency count.
- `runtimeSummary.queue` now includes lightweight operational judgment too: `severity` (`ok|warn|high`) plus short `hints` for backlog/worker/error situations.
- `runtimeSummary.queue` now also distinguishes `invalid_lines`, `retrying_lines`, and `max_retry_seen`, so normal runtime payloads can hint at queue corruption vs poison-item retry churn without a full doctor pass.
- Prompt-time auto-hydration can now be scoped per OpenClaw agent via plugin env vars:
  - `OCMEMOG_AUTO_HYDRATION_ALLOW_AGENT_IDS=agent-a,agent-b`
  - `OCMEMOG_AUTO_HYDRATION_DENY_AGENT_IDS=agent-x`
  - ingest/checkpoint hooks remain global; only `before_prompt_build` hydration is agent-scoped
  - the active auto-hydration policy is surfaced in `runtimeSummary.auto_hydration`
  - plugin-side decision reasons now distinguish `disabled_globally`, `denied_by_agent_id`, `not_in_allowlist`, `allowed_by_allowlist`, and `allowed_globally` for easier debugging
  - plugin logs now include structured decision context for both skipped and applied prompt hydration, including agent id, reason, and prepend sizes
- `/memory/search` now also returns `searchDiagnostics` with lightweight operator-facing retrieval metadata such as strategy, lane, bucket counts, result counts, query token count, elapsed time, vector-search diagnostics (`scan_limit`, `prefilter_limit`, candidate rows, fallback usage), and an `execution_path` block that clarifies provider-configured vs provider-skipped vs local-fallback-expected vs route-exception-fallback behavior.
- `searchDiagnostics.execution_path` now also promotes key embedding outcome fields (`provider_attempted`, `embedding_generated`, `embedding_path_used`, `local_fallback_used`) so the top-level request summary is easier to scan without drilling into nested vector diagnostics.
- `searchDiagnostics.vector_search.embedding` now carries per-request embedding execution details such as whether a provider was attempted, whether local fallback was actually used, what path won (`provider`, `local_simple`, `local_model`), and whether an embedding was generated at all.
- `searchDiagnostics` now also includes `governance_rollup` so operators can quickly see visible result status counts, how many returned items still need governance review, and per-bucket visible rollups for categories such as `knowledge`, `runbooks`, or `lessons`.
- `searchDiagnostics.retrieval_governance` now reports how many candidates were hidden before return because governance marked them `superseded` or `duplicate`, including per-bucket breakdowns such as `knowledge`, `runbooks`, or `lessons`.
- Retrieval results now include a compact `governance_summary` alongside the full governance payload so dashboards/operators can quickly see status, canonical/relationship references, contradiction count, and `needs_review` without unpacking the full provenance structure.
- `/memory/governance/review/summary` now returns `reviewDiagnostics` so operators can see cache hit/freshness, item count, kind breakdown, and active filters without inferring from the raw item list.
- `/memory/governance/review` items now include an `explanation` block with a short human-facing rationale plus source/target memory status, so dashboards and operators do not have to reconstruct meaning from raw fields alone.
- Governance review items now also include a normalized `priority_label` (`none|low|medium|high|critical`), and review summary diagnostics include `priority_label_counts` for quick operator triage.
- `/memory/auto_hydration/policy` accepts an `agent_id` and returns the current prompt-time hydration decision (`allowed`, `reason`, allowlist, denylist, and scoping state) so agent-specific continuity policy can be debugged from the sidecar.

## What is safe to rely on

- `store.init_db()` creates the local schema automatically
- `retrieval.retrieve_for_queries()` is the main sidecar search path
- search is hybrid-ranked, not substring-only:
  - lexical scoring blends exact match, token overlap, ordered phrase overlap, and light prefix matching
  - semantic scoring comes from `vector_index.search_memory()` across the selected embedded categories
  - final ranking also considers reinforcement history, promotion confidence, recency, and optional lane bonuses
- `vector_index.search_memory()` remains a bounded semantic scan rather than a full ANN index
  - it now supports a lightweight lexical prefilter before cosine ranking
  - `OCMEMOG_SEARCH_VECTOR_SCAN_LIMIT` bounds the candidate window
  - `OCMEMOG_SEARCH_VECTOR_PREFILTER_LIMIT` bounds the lexically-biased shortlist used before cosine scoring
- `probe_runtime()` exposes missing shim replacements and optional embedding warnings

## What is not safe to rely on yet

- Provider-backed embeddings
  - Available when `OCMEMOG_EMBED_MODEL_PROVIDER=local-openai` and the local embedding endpoint is reachable.
  - Legacy OpenAI-hosted embeddings remain available when `OCMEMOG_EMBED_MODEL_PROVIDER=openai` and `OCMEMOG_OPENAI_API_KEY` is set.
- Model-backed distillation
  - Available when `OCMEMOG_OPENAI_API_KEY` is set; otherwise falls back to heuristic distill.
- Role-prioritized context building
  - `brain.runtime.roles.role_registry` is now provided by `ocmemog.runtime.roles` and mirrored in compatibility probes.
- Full brAIn memory parity
  - The repo ships only a subset of the original runtime architecture.

## Suggested local workflows

To inspect memory state quickly:

```bash
python3 - <<'PY'
from ocmemog.runtime.memory import store
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
from ocmemog.runtime.memory import vector_index
print(vector_index.index_memory())
PY
```

## TODO: Missing runtime dependencies

- TODO: wire a real inference backend before enabling distill/promote as an operator-facing workflow
- Provider execution is now native-first through `OCMEMOG_EMBED_MODEL_PROVIDER`; `BRAIN_EMBED_MODEL_PROVIDER` remains as a compatibility alias.
- Role-based context selection now has native ownership coverage via `ocmemog.runtime.roles` with shim-aware capability reporting.
- TODO: harden `/memory/get` with a table allow-list before exposing the sidecar outside trusted local use
- TODO: decide whether to expose `runbooks` and `lessons` in the plugin API
