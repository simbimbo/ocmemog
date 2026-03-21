# Contributing

Thanks for improving ocmemog.

## Local development

```bash
git clone https://github.com/simbimbo/ocmemog.git
cd ocmemog
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Useful checks

Run the main local validation steps before opening a PR:

```bash
bash -n scripts/install-ocmemog.sh
bash -n scripts/ocmemog-install.sh
./scripts/install-ocmemog.sh --help
python -m unittest tests.test_regressions
npm pack --dry-run
```

## Installer notes

The primary local bootstrap path is:

```bash
./scripts/install-ocmemog.sh
```

Optional prereq auto-install on macOS/Homebrew:

```bash
OCMEMOG_INSTALL_PREREQS=true ./scripts/install-ocmemog.sh
```

Dry-run mode:

```bash
./scripts/install-ocmemog.sh --dry-run
```

## Releases

Before creating a release:
- update version metadata consistently
- update `CHANGELOG.md`
- verify `README.md` install examples are still correct
- run local validation checks
- run the release gate and review output:
  - `./scripts/ocmemog-release-check.sh`
- create/push the Git tag and GitHub release

## Scope

Keep changes focused:
- continuity/hydration correctness
- install/release reliability
- memory pipeline safety and observability

Avoid unrelated refactors unless they directly support reliability or packaging quality.
