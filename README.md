# ocmemog

Seed repo for OpenClaw memory plugin work. This is a direct copy of the brAIn memory package.

## Contents
- `brain/runtime/memory/` (copied from brAIn)
- `openclaw.plugin.json`, `index.ts`, `package.json` (OC plugin scaffold)

## Notes
This package currently depends on other brAIn runtime modules (state_store, instrumentation, inference, model_router, providers, etc.).
We’ll either:
1) Replace those with OpenClaw plugin interfaces, or
2) Create a thin compatibility layer.

