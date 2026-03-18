# ocmemog

**ocmemog** is an advanced memory engine for OpenClaw that combines durable long-term memory, transcript-backed continuity, conversation hydration, checkpoint expansion, and pondering inside a sidecar-based plugin architecture.

It is designed to go beyond simple memory search by providing:
- **durable memory and semantic retrieval**
- **lossless-style conversation continuity**
- **checkpointing, branch-aware hydration, and turn expansion**
- **transcript ingestion with anchored context recovery**
- **pondering and reflection generation**

Architecture at a glance:
- **OpenClaw plugin (`index.ts`)** handles tools and hook integration
- **FastAPI sidecar (`ocmemog/sidecar/`)** exposes memory and continuity APIs
- **SQLite-backed runtime (`brain/runtime/memory/`)** powers storage, hydration, checkpoints, salience ranking, and pondering

## Repo layout

- `openclaw.plugin.json`, `index.ts`, `package.json`: OpenClaw plugin package and manifest.
- `ocmemog/sidecar/`: FastAPI sidecar with `/memory/search` and `/memory/get`.
- `brain/runtime/memory/`: copied brAIn memory package.
- `brain/runtime/`: compatibility shims for state store, instrumentation, redaction, storage paths, and a few placeholder runtime modules needed for importability.
- `scripts/ocmemog-sidecar.sh`: convenience launcher.

## Run the sidecar

```bash
cd /path/to/ocmemog
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
./scripts/ocmemog-sidecar.sh

# then open
# http://127.0.0.1:17890/dashboard
```

## Optional: transcript watcher (auto-ingest)

```bash
# defaults to ~/.openclaw/workspace/memory/transcripts if not set
export OCMEMOG_TRANSCRIPT_DIR="$HOME/.openclaw/workspace/memory/transcripts"
./scripts/ocmemog-transcript-watcher.sh
```

Default bind:

- endpoint: `http://127.0.0.1:17890`
- health: `http://127.0.0.1:17890/healthz`

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
- `BRAIN_EMBED_MODEL_LOCAL` (`simple` by default)
- `BRAIN_EMBED_MODEL_PROVIDER` (`openai` to enable provider embeddings)
- `OCMEMOG_TRANSCRIPT_WATCHER` (`true` to auto-start transcript watcher inside the sidecar)
- `OCMEMOG_TRANSCRIPT_ROOTS` (comma-separated allowed roots for transcript context retrieval; default: `~/.openclaw/workspace/memory`)
- `OCMEMOG_API_TOKEN` (optional; if set, requests must include `x-ocmemog-token` or `Authorization: Bearer ...`)
- `OCMEMOG_AUTO_HYDRATION` (`true` to re-enable prompt-time continuity prepending; defaults to `false` as a safety guard until the host runtime is verified not to persist prepended context into session history)
- `OCMEMOG_LAPTOP_MODE` (`auto` by default; on macOS battery power this slows watcher polling, reduces ingest batch size, and disables sentiment reinforcement unless explicitly overridden)
- `OCMEMOG_USE_OLLAMA` (`true` to use Ollama for distill/inference)
- `OCMEMOG_OLLAMA_HOST` (default: `http://127.0.0.1:11434`)
- `OCMEMOG_OLLAMA_MODEL` (default: `phi3:latest`)
- `OCMEMOG_OLLAMA_EMBED_MODEL` (default: `nomic-embed-text:latest`)
- `OCMEMOG_PROMOTION_THRESHOLD` (default: `0.5`)
- `OCMEMOG_DEMOTION_THRESHOLD` (default: `0.2`)
- `OCMEMOG_PONDER_ENABLED` (default: `true`)
- `OCMEMOG_PONDER_MODEL` (default: `OCMEMOG_MEMORY_MODEL`)
- `OCMEMOG_LESSON_MINING_ENABLED` (default: `true`)

## Security

- Sidecar binds to **127.0.0.1** by default. Keep it local unless you add auth + firewall rules.
- If you expose the sidecar, set `OCMEMOG_API_TOKEN` and pass the header `x-ocmemog-token`.

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
- pull required local Ollama models when Ollama is already installed
- validate `/healthz`

Notes:
- If `OCMEMOG_INSTALL_PREREQS=true` and Homebrew is present, the installer will try to install missing `ollama` and `ffmpeg` automatically.
- If Ollama is not installed and prereq auto-install is off or unavailable, the installer warns and continues; local model support will remain unavailable until Ollama is installed.
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

### 0.1.4 (unreleased / current main)

Package ownership + runtime safety release:
- Publish package under `@simbimbo/memory-ocmemog` instead of the unauthorized `@openclaw` scope
- Keep `memory-ocmemog` as the plugin id for OpenClaw config and enable flows
- Make `before_message_write` ingest sync-safe for OpenClaw's synchronous hook contract
- Default auto prompt hydration to opt-in via `OCMEMOG_AUTO_HYDRATION=true`
- Preserve prior continuity self-healing and polluted-wrapper cleanup behavior

## Release prep / publish

Current intended ClawHub publish command:

```bash
clawhub publish . --slug memory-ocmemog --name "ocmemog" --version 0.1.4 --changelog "Package ownership fix: publish under @simbimbo scope plus runtime safety hardening for sync-safe ingest and auto-hydration guard"
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
        endpoint: http://127.0.0.1:17890
        timeoutMs: 30000
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
- Public release/distribution metadata is still being tightened up

When a richer path is unavailable, the sidecar is designed to fail soft with explicit warnings rather than crash.
