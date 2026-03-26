# ocmemog

**ocmemog** is an advanced memory engine for OpenClaw that combines durable long-term memory, transcript-backed continuity, conversation hydration, checkpoint expansion, and pondering inside a sidecar-based plugin architecture.

It is designed to go beyond simple memory search by providing:
- **durable memory and hybrid retrieval (lexical + semantic)**
- **operator-visible search diagnostics for retrieval and vector-search behavior**
- **bounded vector search with lightweight lexical prefiltering**
- **lossless-style conversation continuity**
- **checkpointing, branch-aware hydration, and turn expansion**
- **transcript ingestion with anchored context recovery**
- **pondering and reflection generation**
- **durable queue behavior that skips malformed queued payloads, bounds poison-item retries, and exposes clearer queue health diagnostics**
- **compact runtime summaries that make provider/fallback/degraded state explicit in sidecar responses**
- **request-level search execution diagnostics that show provider-skip vs local-fallback vs route-fallback behavior**
- **governance review summary diagnostics for cache freshness and review-kind breakdowns**
- **governance review item explanations that make duplicate/contradiction/supersession rationale easier to render**
- **normalized governance priority labels for easier operator triage**
- **a sidecar hydration-policy diagnostics route for agent-specific continuity debugging**
- **compact governance summaries in retrieval results to bridge search and review workflows**
- **governance rollups in search diagnostics for faster operator triage, including per-bucket visible breakdowns**
- **hidden-by-governance suppression counts in retrieval diagnostics, including per-bucket breakdowns**

Architecture at a glance:
- **OpenClaw plugin (`index.ts`)** handles tools and hook integration
- **FastAPI sidecar (`ocmemog/sidecar/`)** exposes memory and continuity APIs
- **SQLite-backed runtime (`ocmemog/runtime/memory/`)** powers storage, hydration, checkpoints, salience ranking, and pondering

Current local runtime architecture note:
- `docs/architecture/local-runtime-2026-03-19.md`

## Repo layout

- `openclaw.plugin.json`, `index.ts`, `package.json`: OpenClaw plugin package and manifest.
- `ocmemog/sidecar/`: FastAPI sidecar with `/memory/search` and `/memory/get`.
- `ocmemog/runtime/`: native runtime surfaces used by the sidecar and memory engine.
- `ocmemog/runtime/memory/`: local memory/runtime package used by the sidecar.
- `brain/`: internal compatibility residue retained for transitional shim paths; not the primary runtime surface.
- `scripts/ocmemog-sidecar.sh`: convenience launcher.

## Run the sidecar

```bash
cd /path/to/ocmemog
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
./scripts/ocmemog-sidecar.sh

# then open
# http://127.0.0.1:17891/dashboard
```

For local development and CI-style test runs, install test dependencies as well:

```bash
cd /path/to/ocmemog
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-test.txt
```

### Run the doctor check

```bash
./.venv/bin/python3 scripts/ocmemog-doctor.py
./.venv/bin/python3 scripts/ocmemog-doctor.py --json
./.venv/bin/python3 scripts/ocmemog-doctor.py --fix create-missing-paths --fix repair-queue
./.venv/bin/python3 scripts/ocmemog-doctor.py --strict --check runtime/imports --check sqlite/schema-access
```

The doctor command currently checks:
- runtime/imports
- state/path-writable
- sqlite/schema-access
- sidecar/http-auth
- queue/health
- sidecar/transcript-watcher
- sidecar/app-import
- sidecar/transcript-roots
- sidecar/env-toggles
- vector/runtime-probe

## Optional: transcript watcher (auto-ingest)

```bash
# defaults:
# - transcript mode: <openclaw-home>/workspace/memory/transcripts
# - session mode: <openclaw-home>/agents/main/sessions (used when OCMEMOG_TRANSCRIPT_DIR is unset)
export OCMEMOG_TRANSCRIPT_DIR="${OPENCLAW_HOME:-$HOME/.openclaw}/workspace/memory/transcripts"
export OCMEMOG_SESSION_DIR="${OPENCLAW_HOME:-$HOME/.openclaw}/agents/main/sessions"
./scripts/ocmemog-transcript-watcher.sh
```

Default bind:

- endpoint: `http://127.0.0.1:17891`
- health: `http://127.0.0.1:17891/healthz`

## Continuity proof / benchmark harness

Run the fixture-driven continuity benchmark:

```bash
cd /path/to/ocmemog
./.venv/bin/python scripts/ocmemog-continuity-benchmark.py \
  --fixture tests/fixtures/continuity_benchmark.json \
  --report reports/continuity-benchmark-latest.json
```

This exercises:
- restart/recovery hydration from persisted SQLite state
- long-thread + ambiguous reply continuity
- salience-ranked checkpoint expansion
- salience-ranked turn expansion

A passing run writes a JSON report with per-scenario checks and an `overall_score` that must meet the configured `continuity_bar`.

Optional environment variables:

- `OCMEMOG_HOST`
- `OCMEMOG_PORT`
- `OCMEMOG_STATE_DIR` (defaults to `<repo>/.ocmemog-state`)
- `OCMEMOG_DB_PATH`
- `OCMEMOG_MEMORY_MODEL` (default: `gpt-4o-mini`)
- `OCMEMOG_OPENAI_API_KEY` (required for model-backed distill)
- `OCMEMOG_OPENAI_API_BASE` (default: `https://api.openai.com/v1`)
- `OCMEMOG_OPENAI_EMBED_MODEL` (default: `text-embedding-3-small`)
- `OCMEMOG_EMBED_MODEL_LOCAL` (`simple` by default; legacy alias: `BRAIN_EMBED_MODEL_LOCAL`)
- `OCMEMOG_EMBED_MODEL_PROVIDER` (`local-openai` to use the local llama.cpp embedding endpoint; `openai` remains available for hosted embeddings; legacy alias: `BRAIN_EMBED_MODEL_PROVIDER`)
- `OCMEMOG_TRANSCRIPT_WATCHER` (`true` to auto-start transcript watcher inside the sidecar)
- `OPENCLAW_HOME` / `OCMEMOG_OPENCLAW_HOME` (optional OpenClaw home override; default fallback is platform-aware: `~/.openclaw` on Unix, `XDG_DATA_HOME/openclaw` when set, or `%APPDATA%/OpenClaw` on Windows)
- `OCMEMOG_TRANSCRIPT_ROOTS` (comma-separated allowed roots for transcript context retrieval; default: `<openclaw-home>/workspace/memory`)
- `OCMEMOG_TRANSCRIPT_DIR` (default: `<openclaw-home>/workspace/memory/transcripts`)
- `OCMEMOG_SESSION_DIR` (default: `<openclaw-home>/agents/main/sessions`)
- `OCMEMOG_TRANSCRIPT_POLL_SECONDS` (poll interval for file/session watcher; default: `30`, or `120` in battery mode)
- `OCMEMOG_INGEST_BATCH_SECONDS` (max lines per watcher batch; default: `30`, or `120` in battery mode)
- `OCMEMOG_INGEST_BATCH_MAX` (max watcher batches before yield; default: `25`, or `10` in battery mode)
- `OCMEMOG_SESSION_GLOB` (default file glob for session sources: `*.jsonl`)
- `OCMEMOG_TRANSCRIPT_GLOB` (default file glob for transcripts: `*.log`)
- `OCMEMOG_INGEST_ASYNC_WORKER` (`true` to keep async ingest queue processing enabled; defaults to `true`)
- `OCMEMOG_INGEST_ASYNC_POLL_SECONDS` (`5` by default)
- `OCMEMOG_INGEST_ASYNC_BATCH_MAX` (`25` by default)
- `OCMEMOG_INGEST_ENDPOINT` (default: `http://127.0.0.1:17891/memory/ingest_async`)
- `OCMEMOG_SHUTDOWN_DRAIN_QUEUE` (`true` to drain remaining queue entries during shutdown; defaults to `false`)
- `OCMEMOG_WORKER_SHUTDOWN_TIMEOUT_SECONDS` (`0.35` by default)
- `OCMEMOG_SHUTDOWN_DUMP_THREADS` (`true` to include worker thread dump output during shutdown joins; defaults to `false`)
- `OCMEMOG_SHUTDOWN_TIMING` (`true` enables shutdown timing logs; defaults to `true`)
- `OCMEMOG_API_TOKEN` (optional; if set, requests must include `x-ocmemog-token` or `Authorization: Bearer ...`; OpenClaw plugin users should also set the plugin `config.token` field)
- `OCMEMOG_AUTO_HYDRATION` (`true` to re-enable prompt-time continuity prepending; defaults to `false` as a safety guard until the host runtime is verified not to persist prepended context into session history)
- `OCMEMOG_AUTO_HYDRATION_ALLOW_AGENT_IDS` (comma-separated `ctx.agentId` allowlist for prompt-time hydration; when set, only matching agents receive before-prompt hydration)
- `OCMEMOG_AUTO_HYDRATION_DENY_AGENT_IDS` (comma-separated `ctx.agentId` denylist for prompt-time hydration; checked before the allowlist so specific agents can be blocked even when global hydration remains enabled)
- `runtimeSummary.auto_hydration` now exposes the active auto-hydration policy so operators can verify agent scoping from sidecar/runtime payloads
- plugin-side hydration gating now has explicit decision reasons (`disabled_globally`, `denied_by_agent_id`, `not_in_allowlist`, `allowed_by_allowlist`, `allowed_globally`) for clearer debugging/logging
- plugin logs now record structured prompt-hydration decision context for both skipped and applied hydration events
- `OCMEMOG_LAPTOP_MODE` (`auto` by default; on macOS battery power this slows watcher polling, reduces ingest batch size, and disables sentiment reinforcement unless explicitly overridden)
- `OCMEMOG_LOCAL_LLM_BASE_URL` (default: `http://127.0.0.1:18080/v1`; local OpenAI-compatible text endpoint, e.g. llama.cpp)
- `OCMEMOG_LOCAL_LLM_MODEL` (default: `qwen2.5-7b-instruct`; matches the active Qwen2.5-7B-Instruct GGUF runtime)
- `OCMEMOG_LOCAL_EMBED_BASE_URL` (default: `http://127.0.0.1:18081/v1`; local OpenAI-compatible embedding endpoint)
- `OCMEMOG_LOCAL_EMBED_MODEL` (default: `nomic-embed-text-v1.5`)
- `OCMEMOG_USE_OLLAMA` (`true` to force legacy Ollama local inference path)
- `OCMEMOG_OLLAMA_HOST` (default: `http://127.0.0.1:11434`; legacy fallback)
- `OCMEMOG_OLLAMA_MODEL` (default: `qwen2.5:7b`; legacy fallback for machines that still use Ollama)
- `OCMEMOG_OLLAMA_EMBED_MODEL` (default: `nomic-embed-text:latest`; legacy embedding fallback)
- `OCMEMOG_PROMOTION_THRESHOLD` (default: `0.5`)
- `OCMEMOG_DEMOTION_THRESHOLD` (default: `0.2`)
- `OCMEMOG_PONDER_ENABLED` (default: `true`)
- `OCMEMOG_PONDER_MODEL` (default via launcher: `local-openai:qwen2.5-7b-instruct`; recommended for structured local memory refinement)
- `OCMEMOG_LESSON_MINING_ENABLED` (default: `true`)

Boolean env values are parsed case-insensitively and support `1/0`, `true/false`, `yes/no`, `on/off`, `y/n`, and `t/f`.

## Security

- Sidecar binds to **127.0.0.1** by default. Keep it local unless you add auth + firewall rules.
- If you expose the sidecar, set `OCMEMOG_API_TOKEN` and pass the header `x-ocmemog-token`.

## Platform support

- **Core Python package / sidecar:** intended to run cross-platform when Python + SQLite are available.
- **Watcher path defaults:** now resolve from a platform-aware OpenClaw home (`OPENCLAW_HOME` / `OCMEMOG_OPENCLAW_HOME`, XDG, Windows AppData, then legacy `~/.openclaw`).
- **Service/install helpers:** still split by platform.
  - macOS: LaunchAgents supported in-tree
  - Linux: run the sidecar directly with env overrides; if you want a service, create a systemd unit that calls `scripts/ocmemog-sidecar.sh` from your venv
  - Windows: run the sidecar directly with env overrides; use Task Scheduler or NSSM if you need a persistent service

## One‑shot installer (macOS / local dev)

```bash
./scripts/install-ocmemog.sh
```

Optional target checkout directory:

```bash
./scripts/install-ocmemog.sh /custom/path/ocmemog
```

Optional prereq auto-install on macOS/Homebrew systems:

```bash
OCMEMOG_INSTALL_PREREQS=true ./scripts/install-ocmemog.sh
```

Quick help:

```bash
./scripts/install-ocmemog.sh --help
```

This installer will try to:
- clone/update the repo when a custom target directory is provided
- create `.venv`
- install Python requirements
- install/enable the OpenClaw plugin when the `openclaw` CLI is available
- install/load LaunchAgents via `scripts/ocmemog-install.sh`
- verify the local llama.cpp runtime and expected text/embed endpoints
- validate `/healthz`

Notes:
- If `OCMEMOG_INSTALL_PREREQS=true` and Homebrew is present, the installer will try to install missing `llama.cpp` and `ffmpeg` automatically.
- The installer no longer pulls local models. It assumes your llama.cpp text endpoint is on `127.0.0.1:18080` and your embedding endpoint is on `127.0.0.1:18081`.
- Legacy Ollama compatibility remains available only when you explicitly opt into it with `OCMEMOG_USE_OLLAMA=true`.
- If package install is unavailable in the local OpenClaw build, the installer falls back to local-path plugin install.
- Advanced flags are available for local debugging/CI (`--skip-plugin-install`, `--skip-launchagents`, `--skip-model-pulls`, `--endpoint`, `--repo-url`).

## LaunchAgents (macOS)

Templates are included under `scripts/launchagents/`:
- `com.openclaw.ocmemog.sidecar.plist`
- `com.openclaw.ocmemog.ponder.plist`
- `com.openclaw.ocmemog.guard.plist`

You can load them with:
```bash
launchctl bootstrap gui/$UID scripts/launchagents/com.openclaw.ocmemog.sidecar.plist
launchctl bootstrap gui/$UID scripts/launchagents/com.openclaw.ocmemog.ponder.plist
launchctl bootstrap gui/$UID scripts/launchagents/com.openclaw.ocmemog.guard.plist
```

## Recent changes

### 0.1.16 (current main)

Current main now includes:
- integrated release-gate validation with a fresh-state memory contract proof
- live sidecar smoke checks for `/memory/ingest`, `/memory/search`, `/memory/get`, and `/conversation/hydrate`
- hardened async ingest/postprocess handling and queue-health validation
- native-first `ocmemog` config, DB, and report-log naming with legacy fallback support
- sidecar/package version alignment and regression coverage for checkpoint self-heal behavior
- major collapse of the legacy `brain/runtime/*` implementation surface into thin compatibility shims
- removal of orphan legacy memory side-modules and stale architecture references to retired side DBs

## Release prep / publish

Run the release gate first:

```bash
./scripts/ocmemog-release-check.sh
```

This command is the canonical pre-release and CI validation path. A pass now means:
- strict doctor checks succeed on a clean temporary state
- the broad regression subset plus sidecar route tests pass
- live `/healthz`, `/memory/ingest`, `/memory/search`, `/memory/get`, and `/conversation/hydrate` checks pass on `OCMEMOG_RELEASE_LIVE_ENDPOINT`
- the integrated memory contract proof runs in fresh state and emits `reports/release-gate-proof.json`
- syntax checks, integration assertions, and non-blocking packing validation are attempted

Example ClawHub publish command (update version + changelog first; do not reuse stale release text blindly):

```bash
clawhub publish . --slug memory-ocmemog --name "ocmemog" --version <next-version> --changelog "<concise release summary>"
```

## Install from npm (after publish)

```bash
openclaw plugins install @simbimbo/memory-ocmemog
openclaw plugins enable memory-ocmemog
```

## Enable in OpenClaw (local dev)

Add the plugin to your OpenClaw config. The key setting is selecting `memory-ocmemog` in the `memory` slot and pointing the plugin entry at this repo.

```yaml
plugins:
  load:
    paths:
      - /path/to/ocmemog
  slots:
    memory: memory-ocmemog
  entries:
    memory-ocmemog:
      enabled: true
      config:
        endpoint: http://127.0.0.1:17891
        timeoutMs: 30000
        # Optional if the sidecar is protected by OCMEMOG_API_TOKEN
        token: your-ocmemog-token
```

Development install:

```bash
openclaw plugins install -l /path/to/ocmemog
openclaw plugins enable memory-ocmemog
```

If your local OpenClaw build also documents a separate `memory.backend` setting, keep that at its current default unless your build explicitly requires a plugin-backed override. The slot selection above is what activates this plugin.

## Current status

ocmemog is usable today for local OpenClaw installs that want a stronger memory layer with durable recall and transcript-backed continuity.

What is working now:
- Search/get against the local SQLite-backed memory store
- Transcript ingestion and anchored context recovery
- Continuity hydration, checkpoint expansion, and salience-ranked expansion flows
- Local sidecar deployment for macOS/OpenClaw development setups

Current limitations before broader public rollout:
- Some advanced inference- and embedding-dependent paths still depend on environment configuration and may degrade to simpler local behavior if provider access is unavailable
- Packaging and install UX are aimed primarily at power users and local developers today
- Distribution and release metadata are now tracked in `package.json`, `CHANGELOG.md`, and the release check workflow.

When a richer path is unavailable, the sidecar is designed to fail soft with explicit warnings rather than crash.
