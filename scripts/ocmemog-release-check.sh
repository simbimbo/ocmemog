#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

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

BROAD_TEST_FILES=(
  tests/test_sidecar_routes.py
  tests/test_regressions.py
  tests/test_pondering_engine.py
  tests/test_doctor.py
  tests/test_governance_queue.py
  tests/test_promotion_governance_integration.py
  tests/test_hybrid_retrieval.py
)
LIVE_CHECK_URL="${OCMEMOG_RELEASE_LIVE_ENDPOINT:-http://127.0.0.1:17891}"
PROOF_REPORT_DIR="${OCMEMOG_RELEASE_PROOF_DIR:-${ROOT_DIR}/reports}"
PROOF_REPORT_FILE="${PROOF_REPORT_DIR}/release-gate-proof.json"
PROOF_LEGACY_ENDPOINT="${OCMEMOG_RELEASE_LEGACY_ENDPOINT:-}"
LIVE_STATE_DIR="$(mktemp -d -t ocmemog-release-live-XXXXXX)"
DOCTOR_STATE_DIR="$(mktemp -d -t ocmemog-release-doctor-XXXXXX)"
SMOKE_STATE_DIR="$(mktemp -d -t ocmemog-release-smoke-XXXXXX)"
SMOKE_LOG_FILE="${SMOKE_STATE_DIR}/sidecar-smoke.log"
SMOKE_SIDECAR_PID=""
mkdir -p "$PROOF_REPORT_DIR"
cleanup_release_check() {
  if [[ -n "${SMOKE_SIDECAR_PID:-}" ]]; then
    kill "${SMOKE_SIDECAR_PID}" >/dev/null 2>&1 || true
    wait "${SMOKE_SIDECAR_PID}" >/dev/null 2>&1 || true
  fi
  rm -rf "$DOCTOR_STATE_DIR" "$LIVE_STATE_DIR" "$SMOKE_STATE_DIR"
}
trap cleanup_release_check EXIT

STATUS=0

run_step() {
  local label="$1"
  shift
  echo
  echo "[ocmemog-release-check] ${label}"
  if "$@"; then
    echo "[PASS] ${label}"
  else
    local rc=$?
    STATUS=1
    echo "[FAIL] ${label} (status=${rc})"
    return $rc
  fi
}

run_optional_step() {
  local label="$1"
  shift
  echo
  echo "[ocmemog-release-check] ${label}"
  if "$@"; then
    :
  else
    echo "[WARN] ${label}: command reported a non-blocking warning"
  fi
}

start_local_smoke_sidecar() {
  local smoke_port="${OCMEMOG_RELEASE_SMOKE_PORT:-17931}"
  local smoke_host="127.0.0.1"
  export OCMEMOG_STATE_DIR="$SMOKE_STATE_DIR"
  export OCMEMOG_TRANSCRIPT_WATCHER="false"
  export OCMEMOG_INGEST_ASYNC_WORKER="true"
  export OCMEMOG_AUTO_HYDRATION="false"
  export OCMEMOG_SEARCH_SKIP_EMBEDDING_PROVIDER="true"
  export OCMEMOG_HOST="$smoke_host"
  export OCMEMOG_PORT="$smoke_port"
  export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

  "$PYTHON_BIN" -m uvicorn ocmemog.sidecar.app:app --host "$smoke_host" --port "$smoke_port" >"$SMOKE_LOG_FILE" 2>&1 &
  SMOKE_SIDECAR_PID=$!
  LIVE_CHECK_URL="http://${smoke_host}:${smoke_port}"
  export LIVE_CHECK_URL
}

run_step "Verifying shell script syntax" \
  bash -n scripts/install-ocmemog.sh \
  && bash -n scripts/ocmemog-install.sh

run_step "Checking installer command surfaces" \
  ./scripts/install-ocmemog.sh --help \
  && ./scripts/install-ocmemog.sh --dry-run

run_step "Running strict doctor checks against temporary state" \
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

run_optional_step "Running transcript-root diagnostics (non-blocking)" \
  bash -c '
    if "$0" scripts/ocmemog-doctor.py \
      --json \
      --state-dir "$1" \
      --check sidecar/transcript-roots; then
      :
    else
      rc=$?
      if [[ "$rc" -eq 1 ]]; then
        echo "transcript-root diagnostics reported warning."
      elif [[ "$rc" -eq 2 ]]; then
        echo "transcript-root diagnostics reported failure-level issue."
      else
        echo "transcript-root diagnostics exited with unexpected status ${rc}."
      fi
    fi
' "${PYTHON_BIN}" "$DOCTOR_STATE_DIR"

run_optional_step "Running optional runtime probe (non-blocking warning)" \
  bash -c '
    if "$0" scripts/ocmemog-doctor.py --json --state-dir "$1" --check vector/runtime-probe; then
      :
    else
      rc=$?
      if [[ "$rc" -eq 1 ]]; then
        echo "runtime probe reports warning-level status (sidecar may be unavailable in this environment)."
      elif [[ "$rc" -eq 2 ]]; then
        echo "runtime probe reports fail-level status (likely unavailable in this environment)."
      else
        echo "runtime probe exited with unexpected status ${rc}."
      fi
    fi
' "${PYTHON_BIN}" "$DOCTOR_STATE_DIR"

run_step "Checking test dependencies for route tests" \
  "$PYTHON_BIN" - <<'PY'
import importlib.util

missing = [
    name
    for name in ("pytest", "httpx")
    if importlib.util.find_spec(name) is None
]
if missing:
    raise SystemExit("missing test dependencies: " + ", ".join(missing))
PY

run_step "Running broad regression subset" \
  "$PYTHON_BIN" -m pytest -q "${BROAD_TEST_FILES[@]}"

run_step "Running contract-facing sidecar route tests" \
  "$PYTHON_BIN" -m pytest -q tests/test_sidecar_routes.py

if [[ -z "${OCMEMOG_RELEASE_LIVE_ENDPOINT:-}" ]]; then
  echo
  echo "[ocmemog-release-check] No explicit live endpoint provided; starting temporary local sidecar for smoke checks"
  start_local_smoke_sidecar
fi

LIVE_CHECK_ENDPOINT="$LIVE_CHECK_URL"
export LIVE_CHECK_ENDPOINT
run_step "Running live /healthz, /memory/ingest and /memory/search smoke checks" \
  "$PYTHON_BIN" - <<PY
import os
import json
import time
import uuid
import urllib.error
from urllib import request

endpoint = os.environ["LIVE_CHECK_ENDPOINT"].rstrip("/")

def post(path, payload, timeout=10):
    req = request.Request(
        f"{endpoint}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with request.urlopen(req, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)

def get(path, timeout=10):
    req = request.Request(f"{endpoint}{path}")
    with request.urlopen(req, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)

def wait_ready(timeout=20):
    deadline = time.time() + timeout
    health_payload = {}
    while time.time() < deadline:
        try:
            health = get("/healthz", timeout=3)
        except Exception:
            health = None
        if isinstance(health, dict):
            health_payload = health
            if health.get("ok"):
                if health.get("ready") is False:
                    print(f"[ocmemog-release-check] healthz degraded mode={health.get('mode')}: continuing smoke checks with warning")
                    return
                return
            # keep probing only for operational OK states
        time.sleep(0.3)
    if health_payload:
        if not isinstance(health_payload, dict) or not health_payload.get("ok"):
            raise SystemExit(f"healthz did not return ok: {health_payload}")
        raise SystemExit(f"healthz did not become ready after {timeout}s (mode={health_payload.get('mode')})")
    raise SystemExit("healthz did not become ready")

def drain_queue():
    post("/memory/ingest_flush", {"limit": 0}, timeout=10)
    deadline = time.time() + 10
    while time.time() < deadline:
        status = get("/memory/ingest_status", timeout=10)
        if int(status.get("queueDepth", 0) or 0) <= 0:
            return
        time.sleep(0.25)
    raise SystemExit("post-process queue did not drain")

wait_ready()

conversation_id = f"release-check-{uuid.uuid4().hex}"
session_id = f"release-check-session-{uuid.uuid4().hex}"
thread_id = f"release-check-thread-{uuid.uuid4().hex}"
token = f"release-check-token-{uuid.uuid4().hex}"

ingest = post(
    "/memory/ingest",
    {
        "content": f"{token} for release gate verification",
        "kind": "memory",
        "memory_type": "knowledge",
        "conversation_id": conversation_id,
        "session_id": session_id,
        "thread_id": thread_id,
    },
    timeout=20,
)
if not ingest.get("ok") or "reference" not in ingest:
    raise SystemExit("memory ingest endpoint failed")
reference = str(ingest.get("reference"))

# /memory/ingest is synchronous for the core write; async post-processing may settle later.
search = post("/memory/search", {"query": token, "limit": 2}, timeout=20)
if not search.get("ok"):
    raise SystemExit("memory search endpoint failed")
results = list(search.get("results") or [])
if not results:
    raise SystemExit("memory search returned no results")
if len(results) > 2:
    raise SystemExit("memory search returned unbounded results")
if not any(str(item.get("reference") or "") == reference for item in results):
    raise SystemExit("memory search did not recall inserted memory reference")

get_response = post("/memory/get", {"reference": reference}, timeout=15)
if not get_response.get("ok"):
    raise SystemExit("memory get endpoint failed")
if token not in str(get_response.get("memory", {}).get("content") or ""):
    raise SystemExit("memory get response did not include ingested content")

hydrate = post(
    "/conversation/hydrate",
    {
        "conversation_id": conversation_id,
        "session_id": session_id,
        "thread_id": thread_id,
        "turns_limit": 2,
        "memory_limit": 2,
    },
    timeout=20,
)
if not hydrate.get("ok"):
    raise SystemExit("conversation hydrate endpoint failed")
if len(hydrate.get("linked_memories") or []) > 2:
    raise SystemExit("conversation hydrate did not compact linked memories")
print(json.dumps({"health": True, "reference": reference, "search_count": len(results)}, sort_keys=True))
PY

run_step "Running integrated OpenClaw memory contract proof (fresh-state)" \
  bash -lc '
    set -e
    cd "$1"
    "$2" scripts/ocmemog-integrated-proof.py \
      --start-sidecar \
      --endpoint "$3" \
      --state-dir "$4" \
      --timeout 180 \
      ${5:+--legacy-endpoint "$5"} \
      >"$6" 2>"$6.stderr"
  ' -- \
  "$ROOT_DIR" "$PYTHON_BIN" "$LIVE_CHECK_URL" "$LIVE_STATE_DIR" "$PROOF_LEGACY_ENDPOINT" "$PROOF_REPORT_FILE"

run_step "Validating proof output" \
  "$PYTHON_BIN" - <<PY
import json
from pathlib import Path

try:
    payload = json.loads(Path("$PROOF_REPORT_FILE").read_text(encoding="utf-8"))
except FileNotFoundError:
    raise SystemExit("proof output file was not produced")
except json.JSONDecodeError as exc:
    raise SystemExit(f"proof output invalid JSON: {exc}")

if not payload.get("ingest_ok"):
    raise SystemExit("proof ingest check failed")
if not payload.get("search_ok"):
    raise SystemExit("proof search check failed")
if not payload.get("get_ok"):
    raise SystemExit("proof get check failed")
if not payload.get("hydrate_ok"):
    raise SystemExit("proof hydrate check failed")
if int(payload.get("search_count") or 0) > 2:
    raise SystemExit("proof search_count must be capped")
if int(payload.get("linked_count") or 0) > 2:
    raise SystemExit("proof linked_count must be compact")
if "reference" not in payload:
    raise SystemExit("proof missing reference")
json.dumps(payload, sort_keys=True)
PY

run_optional_step "Checking npm pack --dry-run (non-blocking)" \
  bash -c "cd \"$ROOT_DIR\" && npm pack --dry-run"

run_step "Running package and syntax proofs" \
  "$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

json.loads(Path("package.json").read_text(encoding="utf-8"))

for candidate in sorted(Path("ocmemog").rglob("*.py")):
    compile(candidate.read_text(encoding="utf-8"), str(candidate), "exec")

for candidate in sorted(Path("scripts").glob("*.py")):
    compile(candidate.read_text(encoding="utf-8"), str(candidate), "exec")
PY

echo
if [[ "$STATUS" -ne 0 ]]; then
  echo "[ocmemog-release-check] RELEASE CHECK FAILED"
  exit 1
fi

echo "[ocmemog-release-check] RELEASE CHECK PASSED"
echo "[ocmemog-release-check] Proof report: $PROOF_REPORT_FILE"
