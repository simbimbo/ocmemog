# Local Runtime Architecture — 2026-03-19

This repo is now documented and operated with a **llama.cpp-first** local runtime architecture.

## Stable loopback-only service split

- OpenClaw gateway/dashboard: `127.0.0.1:17890`
- ocmemog sidecar/dashboard: `127.0.0.1:17891`
- llama.cpp text inference: `127.0.0.1:18080`
- llama.cpp embeddings: `127.0.0.1:18081`

## Active local models

- Text: `Qwen2.5-7B-Instruct-Q4_K_M.gguf`
- Embeddings: `nomic-embed-text-v1.5.Q4_K_M.gguf`

## Configuration direction

Primary local envs:

- `OCMEMOG_LOCAL_LLM_BASE_URL=http://127.0.0.1:18080/v1`
- `OCMEMOG_LOCAL_LLM_MODEL=qwen2.5-7b-instruct`
- `OCMEMOG_LOCAL_EMBED_BASE_URL=http://127.0.0.1:18081/v1`
- `OCMEMOG_LOCAL_EMBED_MODEL=nomic-embed-text-v1.5`

Legacy Ollama knobs may remain in code for compatibility/rollback, but they are **not the primary runtime path**.

## Operational notes

- The sidecar should remain loopback-only by default.
- The old plain dashboard lives at `http://127.0.0.1:17891/dashboard`.
- Memory search and pondering should target the sidecar, not the OpenClaw gateway port.
- Avoid reusing `17890` for the sidecar; that previously caused a routing collision with the OpenClaw dashboard/gateway.
