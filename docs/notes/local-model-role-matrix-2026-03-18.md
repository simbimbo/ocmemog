# Local model role matrix — 2026-03-18

Historical note: this bakeoff was recorded before the local-runtime cutover from Ollama to llama.cpp. Keep the conclusions, but map them onto the current llama.cpp-served GGUF models when using this repo today.

Purpose: document which installed local model is best suited for which `ocmemog` task so background cognition can be smarter without putting heavy/slow models on every path.

Installed local models observed at the time:
- `phi3:latest`
- `qwen2.5:7b`
- `llama3.1:8b`
- embeddings: `nomic-embed-text:latest`

## Intended decision areas
- unresolved-state rewrite
- lesson extraction
- ponder/reflection shaping
- cluster recommendation wording
- fallback/speed path

## Bakeoff results

### Unresolved-state rewrite
- **Winner:** `qwen2.5:7b`
- Why: cleanest concise rewrite, best instruction-following, least rambling.
- Notes:
  - `phi3:latest` tended to be verbose and occasionally hallucination-prone.
  - `llama3.1:8b` produced one outright unusable response ("None found...").

### Lesson extraction
- **Winner:** `qwen2.5:7b`
- Strong alternate: `llama3.1:8b`
- Why: `qwen2.5:7b` produced the clearest operational lesson with good cause/effect preservation.
- Notes:
  - `phi3:latest` was weaker and more generic.

### Cluster insight / recommendation shaping
- **Winner:** `qwen2.5:7b`
- Why: best structured output, least fluff, most concrete recommendation wording.
- Notes:
  - `llama3.1:8b` was decent but more wordy/stylized.
  - `phi3:latest` timed out or underperformed on this task.

## Recommended model-role split
- embeddings: `nomic-embed-text:latest`
- fast fallback cognition: `phi3:latest`
- default structured memory refinement / ponder model: `qwen2.5:7b`
- richer optional background cognition: `llama3.1:8b`

## Operational recommendation
- Current llama.cpp-first equivalent for this repo:
- Set `OCMEMOG_LOCAL_LLM_MODEL=qwen2.5-7b-instruct` and `OCMEMOG_PONDER_MODEL=local-openai:qwen2.5-7b-instruct` for unresolved-state rewrite, lesson extraction, and cluster recommendation shaping.
- Set `OCMEMOG_LOCAL_EMBED_MODEL=nomic-embed-text-v1.5` for embeddings on the `18081` endpoint.
- If you intentionally keep Ollama on another machine, prefer `OCMEMOG_OLLAMA_MODEL=qwen2.5:7b` instead of `phi3`.
- Consider `llama3.1:8b` for optional deeper background cognition passes where latency is acceptable.
