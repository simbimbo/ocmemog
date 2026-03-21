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
- [ ] Confirm README examples reference the current version where applicable

## Validation
- [ ] Install test deps for sidecar route tests: `python3 -m pip install -r requirements-test.txt`
- [ ] `./scripts/ocmemog-release-check.sh`
- [ ] `tests/test_sidecar_routes.py` route-level sidecar confidence checks (healthz, search, get, ingest, hydrate, dashboard, auth)
- [ ] `npm pack --dry-run`

The `ocmemog-release-check` command enforces strict doctor mode for repo-locally safe checks and runs a focused pytest subset.
It also emits a non-blocking runtime probe as an optional signal; review its output for sidecar/HTTP readiness.
GitHub CI runs the same release check command so local and CI validation remain aligned.

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
