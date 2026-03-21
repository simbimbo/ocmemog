# ocmemog Memory Architecture

## What this repo actually ships

ocmemog vendors a subset of brAIn's memory package and wraps it with a small FastAPI sidecar. The authoritative local implementation lives in:

- `ocmemog/runtime/memory/store.py` for the main SQLite schema
- `ocmemog/runtime/memory/retrieval.py` for keyword-first retrieval
- `ocmemog/runtime/memory/vector_index.py` for embeddings and fallback semantic lookup
- `ocmemog/sidecar/app.py` for the plugin-facing HTTP API

Unlike brAIn, this repo does not ship the full cognition/runtime stack. Several modules under `brain/runtime/*` are compatibility shims so `ocmemog/runtime/*` can import cleanly.

## Storage layout

By default, ocmemog stores state under `.ocmemog-state/` at the repo root unless `OCMEMOG_STATE_DIR` overrides it. `BRAIN_STATE_DIR` remains as a legacy compatibility alias and should not be used for new deployments.

Primary files:

- `.ocmemog-state/memory/brain_memory.sqlite3`
- `.ocmemog-state/reports/brain_memory.log.jsonl`
- `.ocmemog-state/data/person_memory.db`
- `.ocmemog-state/data/interaction_memory.db`
- `.ocmemog-state/data/sentiment_memory.db`
- `.ocmemog-state/data/unresolved_state.db`
- `.ocmemog-state/data/memory_graph.db`

The main SQLite database owns these tables:

- Core memory: `knowledge`, `reflections`, `directives`, `tasks`
- Promotion pipeline: `experiences`, `candidates`, `promotions`
- Derived indexes: `memory_index`, `vector_embeddings`
- Supporting records: `memory_events`, `environment_cognition`, `artifacts`, `runbooks`, `lessons`

## Retrieval flow

The current sidecar behavior is simpler than brAIn's full memory architecture:

1. `/memory/search` calls `retrieval.retrieve_for_queries()`.
2. Retrieval scans `knowledge`, `reflections`, `directives`, and `tasks` for substring matches.
3. Result scoring combines:
   - keyword hit: `1.0` on substring match
   - reinforcement bonus: `reward_score * 0.5`
   - confidence bonus: `promotion confidence * 0.3`
4. If `knowledge` has no keyword hit, retrieval falls back to `vector_index.search_memory()`.
5. The sidecar flattens the bucketed results into a plugin-friendly response.

Operational limits:

- Semantic fallback now rehydrates any embedded bucket (`knowledge`, `runbooks`, `lessons`) when there are no keyword hits.
- Default embeddings are local hash vectors (`BRAIN_EMBED_MODEL_LOCAL=simple`), which are deterministic but weak.
- `runbooks`, `lessons`, `directives`, `reflections`, and `tasks` are now included in the default searchable categories and embedding index.

## Write paths

The main repo-local write paths are:

- `reinforcement.log_experience()` writes to `experiences`
- `candidate.create_candidate()` writes to `candidates` and `memory_events`
- `promote.promote_candidate()` writes to `promotions` plus one of `knowledge`, `runbooks`, or `lessons`
- `vector_index.insert_memory()` writes to `memory_index` and `vector_embeddings`
- `memory_links.add_memory_link()` writes link metadata inside the main memory DB
- `person_memory`, `interaction_memory`, `sentiment_memory`, `unresolved_state`, and `memory_graph` each write to separate SQLite files under `.ocmemog-state/data`

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
- `brain_memory.log.jsonl` captures retrieval, embedding, integrity, and promotion events

Known caveat:

- health/integrity now use source coverage against `vector_embeddings`, but operator interpretation still depends on the active embedding backend and any compatibility-shim surfaces reported by runtime probe

## Sidecar contract

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
