# 2026-03-18 — Memory repair, integrity cleanup, and backfill tooling

## Summary
This pass focused on turning `ocmemog` from a noisy/fragile memory stack into a more repairable and laptop-safe system. The work addressed:
- bad default vector rebuild behavior
- misleading health/compat signals
- missing `memory_reference` writer debt
- poor freshness summaries
- lack of an incremental vector backfill path
- battery-unfriendly defaults in the sidecar launcher

## Changes landed

### Embedding and rebuild behavior
- Fixed the vector reindex entrypoint so it defaults to provider-backed Ollama embeddings instead of silently rebuilding weak hash/simple vectors.
- Confirmed local Ollama embeddings (`nomic-embed-text:latest`) are available and produce 768-dim vectors.
- Added a new incremental repair path:
  - `backfill_missing_vectors()` in `brain/runtime/memory/vector_index.py`
  - `scripts/ocmemog-backfill-vectors.py`
- This gives a non-destructive, table-by-table, chunkable way to backfill missing vectors without requiring a full destructive rebuild.

### Integrity and writer correctness
- Fixed `record_reinforcement()` so new `experiences` rows preserve a deterministic `memory_reference`.
- Added repair support for legacy rows missing `memory_reference`.
- Ran integrity repair and backfilled `1807` missing references.
- Fixed duplicate promotion integrity reporting so grouped duplicate counts are reported accurately.

### Health and output quality
- Fixed sidecar compat/health reporting so provider-backed embeddings do not falsely report local hash fallback warnings.
- Cleaned freshness summaries so placeholder content like `promoted`, `candidate_promoted`, `summary`, and `No local memory summary available` do not pollute advisories.
- Junk-only rows now surface as `(needs summary cleanup)` instead of pretending they contain a meaningful summary.

### Laptop/battery-aware behavior
- Added battery-aware defaults to `scripts/ocmemog-sidecar.sh`.
- `OCMEMOG_LAPTOP_MODE=auto|ac|battery` now controls watcher/ingest aggressiveness.
- On battery the sidecar uses slower polling, smaller batches, and disables sentiment reinforcement by default.

## Current integrity state
After writer/reference repair:
- `missing_memory_reference` debt is cleared
- remaining integrity issue is primarily vector backlog:
  - `vector_missing:19935`

Observed coverage snapshot during staged backfill work:
- `knowledge`: 15999 rows, 0 vectors
- `runbooks`: 179 rows, 152 vectors
- `lessons`: 76 rows, 76 vectors
- `directives`: 233 rows, 206 vectors
- `reflections`: 3460 rows, 83 vectors
- `tasks`: 505 rows, 0 vectors

## Why backlog remains
The remaining `vector_missing` debt is mostly historical backlog rather than an active write-path failure. Existing new writes can index correctly; the old corpus simply was never fully rebuilt under the corrected provider-backed embedding path.

## Recommended staged follow-up
For laptop-friendly backlog burn-down, use staged backfills in roughly this order:
1. directives
2. tasks
3. runbooks
4. lessons
5. reflections
6. knowledge last

## Commits from this sweep
- `f3d3dd9` — fix: default vector reindex to ollama embeddings
- `759d23d` — feat: add battery-aware sidecar defaults
- `4a102eb` — fix: clean memory freshness summaries
- `9ee7966` — fix: report duplicate promotion counts accurately
- `8704db9` — fix: preserve and repair experience memory references
- `5dc3cb9` — feat: add incremental vector backfill tooling
