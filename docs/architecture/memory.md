# ocmemog Memory Architecture

## What this repo actually ships

ocmemog ships a repo-local memory implementation with a FastAPI sidecar, while still carrying some compatibility residue from earlier brAIn-derived runtime surfaces. The authoritative local implementation lives in:

- `ocmemog/runtime/memory/store.py` for the main SQLite schema
- `ocmemog/runtime/memory/retrieval.py` for keyword-first retrieval
- `ocmemog/runtime/memory/vector_index.py` for embeddings and fallback semantic lookup
- `ocmemog/sidecar/app.py` for the plugin-facing HTTP API

Unlike brAIn, this repo does not ship the full cognition/runtime stack. Several modules under `brain/runtime/*` are compatibility shims so `ocmemog/runtime/*` can import cleanly.

## Storage layout

By default, ocmemog stores state under `.ocmemog-state/` at the repo root unless `OCMEMOG_STATE_DIR` overrides it. `BRAIN_STATE_DIR` remains as a legacy compatibility alias and should not be used for new deployments.

Primary files:

- `.ocmemog-state/memory/ocmemog_memory.sqlite3`
- `.ocmemog-state/reports/ocmemog_memory.log.jsonl`
- `.ocmemog-state/data/unresolved_state.db`

The main SQLite database owns these tables:

- Core memory: `knowledge`, `reflections`, `directives`, `tasks`
- Promotion pipeline: `experiences`, `candidates`, `promotions`
- Derived indexes: `memory_index`, `vector_embeddings`
- Supporting records: `memory_events`, `environment_cognition`, `artifacts`, `runbooks`, `lessons`

## Retrieval flow

The current sidecar retrieval path is a bounded hybrid ranker rather than a pure substring search:

1. `/memory/search` calls `retrieval.retrieve_for_queries()`.
2. Each query fans into `retrieval.retrieve()` across the selected categories.
3. Lexical ranking now combines:
   - exact substring hit (`1.0` when the full query appears)
   - token overlap ratio
   - ordered phrase/sequence overlap
   - light prefix matching for partial-word queries
4. Semantic ranking runs through `vector_index.search_memory()` across the selected embedded categories.
5. Final scoring blends:
   - keyword score
   - semantic score
   - reinforcement history
   - promotion confidence
   - recency
   - optional lane bonus when lane-aware metadata matches
6. Superseded / duplicate memories are filtered out, contested memories are penalized, and the sidecar flattens the ranked bucketed results into a plugin-friendly response.
7. The sidecar response now includes lightweight `searchDiagnostics` so operators can inspect the active retrieval strategy, lane selection, per-bucket counts, result compaction, elapsed time, vector-search scan/prefilter behavior, and request-level execution path (provider-configured/provider-skipped/local-fallback-expected/route-exception-fallback) without scraping logs.
   - vector search diagnostics now also carry the actual embedding execution outcome for the request (provider attempted, local fallback used, winning path, embedding generated)
   - the top-level execution-path summary now promotes the key embedding outcome fields for faster operator scanning
8. Retrieval items now also carry a compact `governance_summary` so retrieval and governance surfaces share a simpler bridge for status/triage without forcing every consumer to parse the full governance/provenance structure.
9. `/memory/search` diagnostics now include a governance rollup over the visible results so search consumers can quickly see how governance state is affecting the returned set.
   - this now includes both overall visible status counts and per-bucket visible rollups
10. Retrieval diagnostics also track governance-suppressed candidates (`superseded` / `duplicate`) so the search response can explain what governance hid before the visible result set was assembled.
11. Suppression diagnostics now include per-bucket breakdowns so operators can see which memory classes are carrying the most governance cleanup pressure.

Operational limits:

- Retrieval is still bounded to recent rows per category before ranking, so this is not a full-corpus search engine yet.
- Default embeddings are local hash vectors (`OCMEMOG_EMBED_MODEL_LOCAL=simple`; legacy alias: `BRAIN_EMBED_MODEL_LOCAL`), which are deterministic but weak.
- `runbooks`, `lessons`, `directives`, `reflections`, and `tasks` are included in the default searchable categories and embedding index.
- Semantic ranking currently depends on the active embedding backend and the bounded candidate window in `vector_index.search_memory()`.
- Vector search now supports a lightweight lexical prefilter over the bounded scan window before cosine ranking, which improves relevance without changing the no-ANN local-first design.

Queue/async ingest behavior note:

- the async ingest queue is append-only on disk and processed in bounded batches
- malformed queue lines are skipped and acknowledged rather than blocking valid entries behind them
- valid payload failures are retried in-queue with a bounded retry counter before eventual drop/ack to avoid permanent poison-pill blockage
- operational visibility for these cases remains in queue stats / doctor health rather than crashing the sidecar, and doctor now distinguishes malformed queue damage from retrying poison items

## Write paths

The main repo-local write paths are:

- `reinforcement.log_experience()` writes to `experiences`
- `candidate.create_candidate()` writes to `candidates` and `memory_events`
- `promote.promote_candidate()` writes to `promotions` plus one of `knowledge`, `runbooks`, or `lessons`
- `vector_index.insert_memory()` writes to `memory_index` and `vector_embeddings`
- `memory_links.add_memory_link()` writes link metadata inside the main memory DB
- `unresolved_state` writes to a separate SQLite file under `.ocmemog-state/data`; core memory relationships and provenance now live in the main memory DB.

## Distillation and promotion

The brAIn docs describe a richer distill/promote pipeline. In ocmemog today:

- Distillation exists in `ocmemog/runtime/memory/distill.py`
- Model-backed distillation depends on the configured runtime inference provider and may fall back to heuristics when no usable provider is available
- The practical fallback is a first-line heuristic summary plus generated verification prompts
- Promotion is available locally and writes promoted summaries into `knowledge`, `runbooks`, or `lessons`
- Successful promotion also logs a reinforcement event and attempts vector indexing

This means the pipeline is present in code, but only part of it is production-ready.

## Integrity and health

Available support paths:

- `integrity.run_integrity_check()` checks for missing tables, orphan candidates, duplicate promotions, missing memory references, and index mismatches
- `health.get_memory_health()` reports counts and a coarse integrity summary
- `ocmemog_memory.log.jsonl` captures retrieval, embedding, integrity, and promotion events

Known caveat:

- health/integrity now use source coverage against `vector_embeddings`, but operator interpretation still depends on the active embedding backend and any compatibility-shim surfaces reported by runtime probe

## Sidecar contract

The sidecar exposes a compact runtime summary in route payloads so operators can quickly tell whether the sidecar is in ready/degraded mode, which embedding provider path is active, which local embedding model is configured, whether hash-embedding fallback is in effect, what the current queue health snapshot looks like, and how much compatibility residue remains.
That queue snapshot now includes lightweight severity/hints so the normal runtime payload carries some operational judgment instead of raw counters only.
It also now distinguishes invalid queue lines from retrying payloads, which brings a compact slice of doctor-style queue diagnosis into ordinary runtime payloads.
To reduce translation friction with doctor output, the runtime queue snapshot now also carries doctor-style aliases such as `queue_depth` and `queue_backlog_severity`.

Governance review summary responses now also expose lightweight diagnostics so operators can tell whether they are seeing cached data, how many review items are present, and how the queue splits across review kinds without scraping the full list.

Individual governance review items now also carry a compact explanation object so operator surfaces can render human-readable rationale and status context without reverse-engineering the raw review payload.

Review items and review-summary diagnostics now also expose normalized priority labels so operator surfaces can reason about urgency without inventing their own bucket thresholds.

The sidecar exposes:

- `GET /healthz`
- `POST /memory/search`
- `POST /memory/get`
- `POST /memory/ingest`
- `POST /memory/distill`

The sidecar also reports runtime readiness through `mode`, `missingDeps`, `todo`, and `warnings`. That status is important because several copied brAIn modules are shimmed and should be treated as degraded.

## Runtime adapters

ocmemog now uses repo-local runtime adapters for inference + embeddings, with some compatibility residue still present behind the runtime boundary. The primary active surfaces are under `ocmemog/runtime/*` and require environment configuration:

- `ocmemog/runtime/inference.py` → chat/inference routing (OpenAI or local-openai depending on configured provider)
- `ocmemog/runtime/providers.py` → embedding provider routing
- `ocmemog/runtime/model_roles.py` + `model_router.py` → role-to-model and provider routing

Effect on behavior:

- Distillation uses OpenAI when API key is set, otherwise falls back to heuristics
- Embeddings use OpenAI when configured, otherwise fall back to local hash or sentence-transformers
- Role-aware context selection is now supported via `ocmemog.runtime.roles`, with native ownership tracked in runtime compatibility reporting.

## TODO: Missing runtime dependencies

- DONE: add a repo-local `brain.runtime.roles` implementation.
- TODO: decide whether to add additional provider backends beyond OpenAI
