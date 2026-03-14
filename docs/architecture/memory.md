# ocmemog Memory Architecture

## What this repo actually ships

ocmemog vendors a subset of brAIn's memory package and wraps it with a small FastAPI sidecar. The authoritative local implementation lives in:

- `brain/runtime/memory/store.py` for the main SQLite schema
- `brain/runtime/memory/retrieval.py` for keyword-first retrieval
- `brain/runtime/memory/vector_index.py` for embeddings and fallback semantic lookup
- `ocmemog/sidecar/app.py` for the plugin-facing HTTP API

Unlike brAIn, this repo does not ship the full cognition/runtime stack. Several modules under `brain/runtime/*` are compatibility shims so the copied memory package can import cleanly.

## Storage layout

By default, ocmemog stores state under `.ocmemog-state/` at the repo root unless `OCMEMOG_STATE_DIR` or `BRAIN_STATE_DIR` overrides it.

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
- `runbooks` and `lessons` are now included in the default searchable categories and embedding index.

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

- Distillation exists in `brain/runtime/memory/distill.py`
- Model-backed distillation is not available because `brain/runtime/inference.py` is still a shim
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

- health/integrity currently treat `memory_index` as the "vector" coverage source, even though actual embeddings live in `vector_embeddings`

## Sidecar contract

The sidecar exposes:

- `GET /healthz`
- `POST /memory/search`
- `POST /memory/get`

The sidecar also reports runtime readiness through `mode`, `missingDeps`, `todo`, and `warnings`. That status is important because several copied brAIn modules are shimmed and should be treated as degraded.

## Runtime shim boundary

These repo-local modules are placeholders, not full implementations:

- `brain/runtime/inference.py`
- `brain/runtime/model_roles.py`
- `brain/runtime/model_router.py`
- `brain/runtime/providers.py`

Effect on behavior:

- Distillation falls back to heuristics
- Identity extraction is heuristic-only
- Provider-backed embeddings are effectively unavailable
- Role-aware context selection is partially stubbed because `brain.runtime.roles` is not present

## TODO: Missing runtime dependencies

- TODO: replace `brain.runtime.inference` with an OpenClaw-native inference adapter before relying on distillation or name extraction
- TODO: replace `brain.runtime.model_roles` and `brain.runtime.model_router` with real model/provider routing
- TODO: replace `brain.runtime.providers.provider_execute` with a real embedding provider bridge
- TODO: add a repo-local `brain.runtime.roles` implementation or remove role-priority logic from `context_builder`
- TODO: decide whether `runbooks` and `lessons` are first-class plugin memory types and expose them consistently if they are
- TODO: fix `brain/runtime/memory/api.py` or remove it from the supported surface, because it targets a schema this repo does not create
