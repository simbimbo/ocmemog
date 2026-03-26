# Release Checklist

Use this checklist before publishing an ocmemog release.

The release gate is now codified by:

```bash
./scripts/ocmemog-release-check.sh
```

## Versioning
- [ ] Update `package.json` version
- [ ] Ensure release tag matches package version
- [ ] Update `CHANGELOG.md`
- [ ] Confirm README/release docs reflect current versioned package identity and release workflow

## Validation
- [ ] Install test deps for sidecar route tests: `python3 -m pip install -r requirements-test.txt`
- [ ] `./scripts/ocmemog-release-check.sh`
- [ ] If prompt-time hydration behavior changed, validate the plugin gating path too (for example `node --test tests/test_auto_hydration_agent_scope.ts`) so agent-scoped `before_prompt_build` controls are covered
- [ ] If runtime/operator summary surfaces changed, validate the targeted runtime parity tests too (for example `tests/test_namespace_compat.py`) so `runtimeSummary` queue / embedding / auto-hydration blocks stay aligned
- [ ] Verify `tests/test_doctor.py` still passes for doctor health surfaces if you changed check coverage
- [ ] Verify `reports/release-gate-proof.json` exists after a passing gate and documents:
  - live ingest/search/get/hydrate verification
  - capped response selection (`memory/search` and `conversation/hydrate`)
  - reference recall for distinctive injected memory
- [ ] If testing against a protected sidecar, confirm auth-bearing requests succeed (`x-ocmemog-token` or `Authorization: Bearer ...`)
- [ ] `npm pack --dry-run`

The `ocmemog-release-check` command enforces strict doctor mode for repo-locally safe checks, runs a focused pytest subset, validates explicit sidecar route behavior, runs live `/healthz`, `/memory/ingest`, `/memory/search`, `/memory/get`, and `/conversation/hydrate` smoke checks, and executes a full integrated proof in fresh state.
Legacy-state verification is optional and can be enabled with `OCMEMOG_RELEASE_LEGACY_ENDPOINT`.
GitHub CI runs the same release check command so local and CI validation remain aligned.

## Install flow
- [ ] Verify default installer path still works: `./scripts/install-ocmemog.sh`
- [ ] Verify optional prereq install path is documented correctly
- [ ] Verify LaunchAgent load path still matches repo scripts
- [ ] Verify sidecar health check passes after install
- [ ] Verify any new plugin env controls are documented in README/usage/release notes (for example `OCMEMOG_AUTO_HYDRATION_ALLOW_AGENT_IDS` / `OCMEMOG_AUTO_HYDRATION_DENY_AGENT_IDS`)
- [ ] Verify README/usage/release notes still describe the current operator/runtime surfaces (`runtimeSummary`, `searchDiagnostics`, queue snapshot, hydration policy route) accurately after release-bound changes

## Public artifacts
- [ ] Push `main`
- [ ] Create/update GitHub release notes
- [ ] Publish/update ClawHub wrapper skill if installer/docs changed
- [ ] Publish npm package if auth is available

## Post-release sanity
- [ ] Confirm repo default branch contains the release commits
- [ ] Confirm GitHub release URL works
- [ ] Confirm package/install instructions are still accurate
