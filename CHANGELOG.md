# Changelog

## Unreleased

Retrieval ranking quality pass plus collateral/doc alignment.

### Highlights
- improved lexical retrieval scoring to consider token overlap, ordered phrase overlap, and light prefix matching instead of relying on blunt substring-or-overlap behavior
- kept the retrieval path bounded and hybrid by continuing to blend lexical, semantic, reinforcement, promotion, recency, and lane-aware signals
- added lightweight `searchDiagnostics` to `/memory/search` so retrieval strategy, lane, bucket counts, result compaction, timing, and vector-search scan/prefilter details are visible in the API response
- added a bounded lexical prefilter inside `vector_index.search_memory()` so semantic ranking can prefer lexically relevant candidates before cosine scoring without introducing ANN complexity
- aligned README and architecture/usage docs with the actual shipped hybrid retrieval behavior
- added regression coverage for partial-phrase lexical matches, sidecar search diagnostics, vector prefilter behavior, malformed queue-line recovery, bounded async retry behavior, and doctor visibility for retrying queue payloads
- hardened async queue processing so malformed queue JSON is skipped/acknowledged instead of blocking later valid entries in the same queue file
- added bounded retry tracking for valid queue payload failures so poison items are retried a small number of times and then dropped/acknowledged instead of blocking the queue forever
- improved doctor queue health output so malformed queue lines and retrying poison items are reported separately with clearer hints and samples
- added `runtimeSummary` to sidecar/runtime payloads so provider path, hash-fallback state, degraded/ready mode, and compatibility residue are explicit to operators
- expanded `/memory/search` diagnostics with a request-level `execution_path` block so provider-configured, provider-skipped, local-fallback-expected, and route-exception-fallback behavior is explicit per request
- added `reviewDiagnostics` to `/memory/governance/review/summary` so cache freshness, item count, kind breakdown, and active filters are explicit to operators
- added an `explanation` block to `/memory/governance/review` items so per-item rationale and source/target status context are easier to render and review
- added normalized governance `priority_label` values on review items and `priority_label_counts` in review summary diagnostics for simpler operator triage
- added per-agent auto-hydration controls (`OCMEMOG_AUTO_HYDRATION_ALLOW_AGENT_IDS` / `OCMEMOG_AUTO_HYDRATION_DENY_AGENT_IDS`) so prompt-time continuity can be scoped by `ctx.agentId` without disabling global ingest/checkpoint behavior
- surfaced the active auto-hydration agent policy in `runtimeSummary.auto_hydration` for easier operator verification and debugging
- added explicit plugin-side hydration decision reasons so skips can be traced to global disable vs denylist vs allowlist mismatch
- added `/memory/auto_hydration/policy` so operators can query the current agent-specific prompt-hydration decision from the sidecar
- improved plugin hydration observability with structured skip/apply decision logs that include agent id, decision reason, and prepend sizes
- added compact `governance_summary` payloads to retrieval results so search consumers can triage governance state without unpacking the full provenance structure
- enriched `runtimeSummary` embedding observability with local embedding model and embedding path readiness details so provider vs local/simple fallback is clearer to operators
- added request-level embedding execution diagnostics to vector/search responses so operators can tell whether provider embedding was attempted, whether local fallback ran, and which path actually produced the query embedding
- promoted the key embedding outcome fields into `searchDiagnostics.execution_path` so request-level scanability is better without drilling into nested vector diagnostics
- added `governance_rollup` to `/memory/search` diagnostics so search consumers can quickly see visible result status counts and needs-review totals
- extended visible governance rollups with per-bucket breakdowns so search consumers can see where visible governance pressure is concentrated
- added retrieval governance suppression counts so `/memory/search` diagnostics can report how many candidates were hidden as `superseded` or `duplicate`
- extended retrieval governance suppression diagnostics with per-bucket breakdowns so search consumers can see where hidden-governance pressure is concentrated

## 0.1.16 — 2026-03-25

Platform support doc clarification for Linux/Windows service guidance.

### Highlights
- documented Linux systemd and Windows service runner guidance (no new code changes)

## 0.1.15 — 2026-03-25

Hydration stabilization + cross-platform default cleanup.

### Highlights
- made `/conversation/hydrate` read-only (no inline `refresh_state()` on the hot read path)
- added hydrate stage timing + refresh_state source tagging for root-cause clarity
- added plugin prepend-size logging and a dedicated hydrate stress harness
- expanded platform-aware OpenClaw home defaults (OPENCLAW_HOME / OCMEMOG_OPENCLAW_HOME / XDG / Windows AppData)
- updated transcript/test rig helpers and docs to match cross-platform defaults

## 0.1.14 — 2026-03-22

Corrective follow-up to make the published release fully version-aligned.

### Highlights
- aligned package/source/documentation versioning after the 0.1.13 publish so source fallback version, README current-main note, and package metadata all agree on the shipped release
- preserves the full 0.1.13 hardening/test matrix and release validation while fixing version/documentation drift

## 0.1.13 — 2026-03-22

Final hardening release before a possible 1.0 cut.

### Highlights
- tested the full shipped surface aggressively: full pytest suite, release gate, live sidecar contract smoke, packaging dry-run, installer surfaces, and governance summary responsiveness checks all passed together
- fixed a supersession-governance regression that could suppress `supersession_recommendation` generation and break governance queue/review/summary and auto-resolve flows
- moved dashboard supersession plain-English rewriting out of the render path so live dashboard loads stay fast while recommendations still carry human-readable text
- added a lightweight cached governance review summary path for the dashboard, reducing review load time from multi-second scans to sub-second first load and near-instant cached refresh
- simplified Governance Review UI output into more concise, single-row plain-English review items
- hardened supersession summary generation against polluted transcript/log content with tighter preview normalization, aggressive noise stripping, bounded local-model rewriting, and safe heuristic fallbacks
- fixed dashboard cursor handling so the dashboard route tolerates minimal/mock DB cursor implementations used by tests
- hardened the integrated proof token/session identifiers to avoid fresh-state collisions during repeated release validation

## 0.1.12 — 2026-03-21

Release hardening, integrated proof validation, and native-ownership cleanup.

### Highlights
- fixed conversation-state self-healing so polluted continuity cleanup preserves valid checkpoints instead of deleting the entire checkpoint history for a thread/session/conversation
- aligned FastAPI sidecar version reporting with the package version and added regression coverage for version drift
- moved runtime defaults toward native `ocmemog` ownership for report-log and SQLite DB naming while preserving legacy `brain_*` file fallback for existing installs
- made embedding configuration native-first (`OCMEMOG_*`) while keeping `BRAIN_*` aliases for compatibility
- hardened the integrated memory contract proof and release gate so fresh-state proof, live sidecar smoke, and route/regression validation run together as the canonical pre-release bar
- fixed release-gate/proof path bugs around HTTP method mismatches, async ingest/postprocess timing assumptions, and proof output capture
- restored live sidecar request-path verification for `/memory/ingest`, `/memory/search`, `/memory/get`, and `/conversation/hydrate`
- collapsed the legacy `brain/runtime/*` implementation tree into thin compatibility shims and removed orphan legacy side-modules that were no longer part of the shipped product contract
- cleaned release docs, compat wording, and helper scripts so new deployments follow native `ocmemog` behavior by default and stale side-DB architecture references are removed
- removed stray invalid transcript-watcher drift assertions from the test suite

## 0.1.11 — 2026-03-20

Watcher reliability and release-quality follow-up.

### Highlights
- prevented duplicate transcript/session turn ingestion in the watcher path
- propagated `OCMEMOG_API_TOKEN` auth headers on watcher HTTP posts
- restored persisted queue stats on sidecar startup
- added durable watcher error logging instead of silent failure swallowing
- preserved multi-part text content from session message arrays
- fixed transcript target handling for both directory mode and file mode
- hardened retry behavior so failed delivery does not silently drop buffered content and session retries preserve transcript provenance without duplicate transcript rows
- declared `pytest` as a test extra and refreshed release-facing docs/checklists for current validation flow

## 0.1.10 — 2026-03-19

Release alignment follow-up.

### Highlights
- normalized npm repository metadata to the canonical `git+https` form
- aligned repo `main`, git tag/release, and npm package version after the 0.1.9 publish

## 0.1.9 — 2026-03-19

Memory quality, governance, and review release.

### Highlights
- added near-duplicate collapse for transcript/session double-ingest candidate generation
- added conservative reflection reclassification and new durable buckets for `preferences` and `identity`
- wired new buckets through storage, retrieval, embeddings, health, integrity, and promotion/demotion paths
- hardened governance auto-promotion for duplicates and supersessions with stricter thresholds and guardrails
- added governance review endpoints plus dashboard review panel with filters and approve/reject actions
- fixed release-blocking distill fallback behavior in no-model environments and removed stale hard-coded bucket drift

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
