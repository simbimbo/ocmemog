# Release Checklist

Use this checklist before publishing an ocmemog release.

## Versioning
- [ ] Update `package.json` version
- [ ] Ensure release tag matches package version
- [ ] Update `CHANGELOG.md`
- [ ] Confirm README examples reference the current version where applicable

## Validation
- [ ] `bash -n scripts/install-ocmemog.sh`
- [ ] `bash -n scripts/ocmemog-install.sh`
- [ ] `./scripts/install-ocmemog.sh --help`
- [ ] `./scripts/install-ocmemog.sh --dry-run`
- [ ] `./.venv/bin/python -m pytest -q tests/test_regressions.py tests/test_governance_queue.py tests/test_promotion_governance_integration.py tests/test_hybrid_retrieval.py`
- [ ] `npm pack --dry-run`

## Install flow
- [ ] Verify default installer path still works: `./scripts/install-ocmemog.sh`
- [ ] Verify optional prereq install path is documented correctly
- [ ] Verify LaunchAgent load path still matches repo scripts
- [ ] Verify sidecar health check passes after install

## Public artifacts
- [ ] Push `main`
- [ ] Create/update GitHub release notes
- [ ] Publish/update ClawHub wrapper skill if installer/docs changed
- [ ] Publish npm package if auth is available

## Post-release sanity
- [ ] Confirm repo default branch contains the release commits
- [ ] Confirm GitHub release URL works
- [ ] Confirm package/install instructions are still accurate
