# Local model role matrix — 2026-03-18

Purpose: document which installed local model is best suited for which `ocmemog` task so background cognition can be smarter without putting heavy/slow models on every path.

Installed local models observed:
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
- Keep `OCMEMOG_OLLAMA_MODEL=phi3:latest` for lightweight local fallback behavior.
- Set `OCMEMOG_PONDER_MODEL=qwen2.5:7b` for unresolved-state rewrite, lesson extraction, and cluster recommendation shaping.
- Consider `llama3.1:8b` for optional deeper background cognition passes where latency is acceptable.
