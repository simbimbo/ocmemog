# ocmemog

`ocmemog` is a sidecar-based OpenClaw memory plugin. The TypeScript plugin stays in the normal OpenClaw plugin scaffold, and a local FastAPI sidecar hosts the copied brAIn-derived memory package.

## Repo layout

- `openclaw.plugin.json`, `index.ts`, `package.json`: OpenClaw plugin scaffold.
- `ocmemog/sidecar/`: FastAPI sidecar with `/memory/search` and `/memory/get`.
- `brain/runtime/memory/`: copied brAIn memory package.
- `brain/runtime/`: compatibility shims for state store, instrumentation, redaction, storage paths, and a few placeholder runtime modules needed for importability.
- `scripts/ocmemog-sidecar.sh`: convenience launcher.

## Run the sidecar

```bash
cd /Users/simbimbo/ocmemog
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
./scripts/ocmemog-sidecar.sh
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

Optional environment variables:

- `OCMEMOG_HOST`
- `OCMEMOG_PORT`
- `OCMEMOG_STATE_DIR` (defaults to `/Users/simbimbo/ocmemog/.ocmemog-state`)
- `OCMEMOG_DB_PATH`
- `OCMEMOG_MEMORY_MODEL` (default: `gpt-4o-mini`)
- `OCMEMOG_OPENAI_API_KEY` (required for model-backed distill)
- `OCMEMOG_OPENAI_API_BASE` (default: `https://api.openai.com/v1`)
- `OCMEMOG_OPENAI_EMBED_MODEL` (default: `text-embedding-3-small`)
- `BRAIN_EMBED_MODEL_LOCAL` (`simple` by default)
- `BRAIN_EMBED_MODEL_PROVIDER` (`openai` to enable provider embeddings)
- `OCMEMOG_TRANSCRIPT_WATCHER` (`true` to auto-start transcript watcher inside the sidecar)

## Enable in OpenClaw

Add the plugin to your OpenClaw config. The key setting is selecting `memory-ocmemog` in the `memory` slot and pointing the plugin entry at this repo.

```yaml
plugins:
  load:
    paths:
      - /Users/simbimbo/ocmemog
  slots:
    memory: memory-ocmemog
  entries:
    memory-ocmemog:
      enabled: true
      config:
        endpoint: http://127.0.0.1:17890
        timeoutMs: 10000
```

Development install:

```bash
openclaw plugins install -l /Users/simbimbo/ocmemog
openclaw plugins enable memory-ocmemog
```

If your local OpenClaw build also documents a separate `memory.backend` setting, keep that at its current default unless your build explicitly requires a plugin-backed override. The slot selection above is what activates this plugin.

## Current compatibility status

The sidecar starts and the endpoints run even when the full original brAIn runtime is not present.

- Search/get use the local SQLite store and retrieval path where possible.
- Provider-backed embeddings, distillation, and some inference-driven flows are still shimmed.
- When a full path is not available yet, the sidecar returns a clear `TODO` response with `missingDeps` and `warnings` instead of crashing.
