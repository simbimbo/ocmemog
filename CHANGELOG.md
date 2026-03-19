# Changelog

## 0.1.8 — 2026-03-19

Documentation and release follow-through after the llama.cpp migration and repo grooming pass.

### Highlights
- documented the stable local runtime architecture (gateway/sidecar/text/embed split)
- published the repo in a llama.cpp-first state with fixed ports and cleaned installers/scripts
- kept compatibility hooks only where still useful instead of leaving Ollama as the implied primary path

## 0.1.7 — 2026-03-19

llama.cpp-first cleanup after the 0.1.6 runtime cutover.

### Highlights
- made llama.cpp / local OpenAI-compatible endpoints the primary documented and scripted local runtime path
- reduced misleading Ollama-first defaults in installers, sidecar scripts, docs, and helper tooling
- aligned context/distill/runtime helpers with the fixed local model architecture (`17890` gateway, `17891` sidecar, `18080` text, `18081` embeddings)
- kept compatibility hooks only where still useful for rollback or mixed environments

## 0.1.6 — 2026-03-19

Port-separation and publish-solid follow-up.

### Highlights
- Split ocmemog sidecar onto dedicated loopback port `17891` to avoid collision with the OpenClaw gateway/dashboard on `17890`
- Restored the plain realtime dashboard on `/dashboard` and fixed the `local_html` template crash
- Updated plugin/runtime defaults, scripts, and documentation to use the dedicated sidecar endpoint on `17891`
- Switched repo-facing local-runtime defaults to llama.cpp-first endpoints on `18080`/`18081` with Qwen2.5 text and `nomic-embed-text-v1.5` embeddings, while keeping Ollama as explicit legacy fallback only
- Added governance retrieval/governance-policy hardening plus expanded regression coverage for duplicate, contradiction, supersession, queue, audit, rollback, and auto-resolve flows
- Aligned package/version metadata across npm, Python, and FastAPI surfaces

## 0.1.5 — 2026-03-18

Repair and hardening follow-up after the 0.1.4 publish.

### Highlights
- Fixed vector reindex defaults so repair scripts use provider-backed local embeddings instead of silently rebuilding weak local/hash vectors
- Added battery-aware sidecar defaults for macOS laptops (`OCMEMOG_LAPTOP_MODE=auto|ac|battery`)
- Fixed `record_reinforcement()` so new experiences preserve `memory_reference`, and added integrity repair to backfill legacy missing references
- Added incremental vector backfill tooling (`scripts/ocmemog-backfill-vectors.py`) for non-destructive backlog repair
- Cleaned freshness summaries so junk placeholders (`promoted`, `summary`, `No local memory summary available`) do not pollute advisories
- Improved integrity reporting to count duplicate promotion groups accurately

### Notes
- Historical vector backlog still exists and should be burned down in staged backfills, especially for `knowledge`
- Detailed repair notes: `docs/notes/2026-03-18-memory-repair-and-backfill.md`

## 0.1.4 — 2026-03-18

Package ownership + runtime safety release.

### Highlights
- Renamed npm package to `@simbimbo/memory-ocmemog` so it can be published under Steven's own npm scope
- Updated installer/docs to use the `@simbimbo` package while keeping plugin id `memory-ocmemog` unchanged
- Preserved the OpenClaw runtime safety hardening from 0.1.3 (sync-safe ingest + auto-hydration opt-in guard)

## 0.1.3 — 2026-03-18

OpenClaw runtime safety hardening release.

### Highlights
- Made `before_message_write` continuity ingest sync-safe for OpenClaw's synchronous hook contract
- Disabled automatic prompt hydration by default unless `OCMEMOG_AUTO_HYDRATION=true` is explicitly set
- Kept sidecar-backed memory search/ingest/checkpoint flows active while guarding against context-window blowups from prepended continuity wrappers
- Added startup logging so hosts can see when auto hydration is intentionally disabled

## 0.1.2 — 2026-03-17

Continuity hydration hardening release.

### Highlights
- Prevented recursive re-ingest of auto-hydrated continuity wrappers into conversational state
- Kept short confirmation replies like `ok`, `yes`, and `sure` compact in `latest_user_ask` / `latest_user_intent`
- Changed hydration to prefer unresolved assistant commitments only
- Ignored oversized/noisy checkpoint summaries during hydration selection
- Normalized sender envelopes, reply tags, and polluted multi-timestamp wrapper text before they could pollute state
- Added self-healing cleanup for legacy poisoned turns/checkpoints during refresh
- Hardened `memory_links` unique-index setup against duplicate legacy rows
- Added `.DS_Store` ignore hygiene for the repo

### Included commits
- `231cfcb` — fix: harden continuity hydration against recursive contamination
- `3db6891` — fix: keep short reply intent compact in hydration state
- `fe49663` — fix: prefer unresolved assistant commitments in hydration
- `74a44fc` — fix: drop oversized checkpoint summaries from hydration
- `4b89fc1` — chore: ignore macOS Finder metadata files

## 0.1.1 — 2026-03-16

Publish-prep release.

### Highlights
- Cleaned package metadata for public release
- Corrected repository and homepage links to the actual `simbimbo/ocmemog` repo
- Removed "scaffold" positioning from release-facing package metadata
- Tightened README wording around current status and install flow
- Excluded Python cache artifacts, tests, reports, and review notes from the published package
- Verified clean package output with `npm pack --dry-run`
- Re-ran continuity benchmark with passing score (`overall_score: 1.0`)

### Intended publish command
```bash
clawhub publish . --slug memory-ocmemog --name "ocmemog" --version 0.1.1 --changelog "Initial public release: durable memory, transcript-backed continuity, packaging cleanup, and publish-ready metadata"
```
