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

Default bind:

- endpoint: `http://127.0.0.1:17890`
- health: `http://127.0.0.1:17890/healthz`

Optional environment variables:

- `OCMEMOG_HOST`
- `OCMEMOG_PORT`
- `OCMEMOG_STATE_DIR`
- `OCMEMOG_DB_PATH`
- `BRAIN_EMBED_MODEL_LOCAL` (`simple` by default)

## Enable in OpenClaw

Add the plugin to your OpenClaw config. The important part is assigning this repo to the `memory` plugin slot, then selecting the memory backend if your setup expects it.

```yaml
plugins:
  slots:
    memory: /Users/simbimbo/ocmemog
  entries:
    memory-ocmemog:
      endpoint: http://127.0.0.1:17890
      timeoutMs: 10000
```

If your OpenClaw build expects an explicit memory backend, use the plugin slot above and keep `memory.backend` set to its default (builtin) until we finalize the plugin adapter.

## Current compatibility status

The sidecar starts and the endpoints run even when the full original brAIn runtime is not present.

- Search/get use the local SQLite store and retrieval path where possible.
- Provider-backed embeddings, distillation, and some inference-driven flows are still shimmed.
- When a full path is not available yet, the sidecar returns a clear `TODO` response with `missingDeps` and `warnings` instead of crashing.
