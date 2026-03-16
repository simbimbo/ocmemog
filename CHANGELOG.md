# Changelog

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
