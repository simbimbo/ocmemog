# Changelog

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
