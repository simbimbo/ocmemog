#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
TEST_REQUIREMENTS_FILE="${TEST_REQUIREMENTS_FILE:-requirements-test.txt}"

PYTHON_BIN="${OCMEMOG_PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x ".venv/bin/python3" ]]; then
    PYTHON_BIN=".venv/bin/python3"
  else
    PYTHON_BIN="$(command -v python3)"
  fi
fi
if [[ -z "${PYTHON_BIN}" ]] || ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "A python executable could not be found. Set OCMEMOG_PYTHON_BIN to a valid interpreter path."
  exit 1
fi

TEST_FILE_ARGS=(
  tests/test_sidecar_routes.py
  tests/test_regressions.py
  tests/test_governance_queue.py
  tests/test_promotion_governance_integration.py
  tests/test_hybrid_retrieval.py
)
NPM_PACK_LOG="/tmp/ocmemog-npm-pack.log"

DOCTOR_STATE_DIR="$(mktemp -d -t ocmemog-release-doctor-XXXXXX)"
trap 'rm -rf "$DOCTOR_STATE_DIR"; rm -f "$NPM_PACK_LOG"' EXIT

run_step() {
  local name="$1"
  shift
  echo
  echo "[ocmemog-release-check] ${name}"
  "$@"
}

run_step "Verifying shell script syntax"
bash -n scripts/install-ocmemog.sh
bash -n scripts/ocmemog-install.sh

run_step "Checking installer command surfaces"
./scripts/install-ocmemog.sh --help
./scripts/install-ocmemog.sh --dry-run

run_step "Running strict doctor checks against temporary state"
"$PYTHON_BIN" scripts/ocmemog-doctor.py \
  --json \
  --strict \
  --state-dir "$DOCTOR_STATE_DIR" \
  --check runtime/imports \
  --check state/path-writable \
  --check sqlite/schema-access \
  --check queue/health \
  --check sidecar/env-toggles \
  --check sidecar/app-import

run_step "Running transcript-root diagnostics (non-blocking)"
if "$PYTHON_BIN" scripts/ocmemog-doctor.py \
  --json \
  --state-dir "$DOCTOR_STATE_DIR" \
  --check sidecar/transcript-roots; then
  :
else
  status=$?
  if [[ "$status" -eq 1 ]]; then
    echo "WARN: transcript-root diagnostics reported warning (expected in CI/local clean-room environments)."
  elif [[ "$status" -eq 2 ]]; then
    echo "WARN: transcript-root diagnostics reported failure-level issue."
  else
    echo "WARN: transcript-root diagnostics exited with unexpected status ${status}."
  fi
fi

run_step "Running optional runtime probe (non-blocking warning)"
if "$PYTHON_BIN" scripts/ocmemog-doctor.py --json --state-dir "$DOCTOR_STATE_DIR" --check vector/runtime-probe; then
  :
else
  status=$?
  if [[ "$status" -eq 1 ]]; then
    echo "WARN: runtime probe reports warning-level status (sidecar may be unavailable in this environment)."
  elif [[ "$status" -eq 2 ]]; then
    echo "WARN: runtime probe reports fail-level status (likely unavailable in this environment)."
  else
    echo "WARN: runtime probe exited with unexpected status ${status}."
  fi
fi

run_step "Verifying test dependencies for route tests"
if ! "$PYTHON_BIN" - <<'PY'
import importlib.util

missing = [
    name
    for name in ("pytest", "httpx")
    if importlib.util.find_spec(name) is None
]
if missing:
    raise SystemExit("missing test dependencies: " + ", ".join(missing))
PY
then
  echo "ERROR: pytest and/or httpx missing. Install with:"
  echo "  $PYTHON_BIN -m pip install -r \"$ROOT_DIR/$TEST_REQUIREMENTS_FILE\""
  exit 1
fi

run_step "Running pytest release subset"
"$PYTHON_BIN" -m pytest -q "${TEST_FILE_ARGS[@]}"

run_step "Running package and syntax proofs"
"$PYTHON_BIN" -m json.tool package.json >/dev/null
"$PYTHON_BIN" - <<'PY'
from pathlib import Path

for candidate in sorted(Path("ocmemog").rglob("*.py")):
    compile(candidate.read_text(encoding="utf-8"), str(candidate), "exec")

for candidate in sorted(Path("scripts").glob("*.py")):
    compile(candidate.read_text(encoding="utf-8"), str(candidate), "exec")
PY
if ! npm pack --dry-run >"$NPM_PACK_LOG"; then
  echo "WARN: npm pack --dry-run failed in this environment. See /tmp/ocmemog-npm-pack.log."
  tail -n 20 "$NPM_PACK_LOG"
else
  cat "$NPM_PACK_LOG"
fi

echo
echo "[ocmemog-release-check] release check complete"
