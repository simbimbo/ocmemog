# ocmemog Memory Review

## Scope and assumptions

- Reviewed line-by-line: `brain/runtime/memory/*`, `ocmemog/sidecar/*`, and the runtime shim layer under `brain/runtime/*`.
- Intent cross-reference source: `/Users/simbimbo/brain/docs/architecture/memory.md` plus related state-file notes.
- Assumption: `brain/runtime/memory/store.py` is the authoritative schema for this repo. Any module expecting legacy brAIn tables or control-plane services is a drift point unless ocmemog adds the missing dependency locally.
- Assumption: current user-facing surface is the FastAPI sidecar (`/healthz`, `/memory/search`, `/memory/get`), not the full brAIn cognition loop.

## Summary findings

- Fixed: `brain/runtime/memory/api.py` now matches the local schema (`memory_events`, `tasks`, `knowledge`, `experiences`).
- Fixed: `ocmemog/sidecar/app.py` now enforces a table allow-list in `_get_row()`.
- High risk: distillation, identity extraction, and provider-backed embeddings are advertised by the copied package but are still shim-backed in this repo. `brain/runtime/inference.py`, `brain/runtime/model_roles.py`, and `brain/runtime/providers.py` do not implement real runtime behavior.
- Fixed: health/integrity now report coverage using `vector_embeddings`.
- Medium risk: some modules are effectively orphaned from the sidecar flow (`memory_synthesis`, `memory_gate`, `memory_graph`, `semantic_search`, `tool_catalog`, `unresolved_state`) and currently document capability more than they deliver.

## File-by-file review

### `brain/runtime/__init__.py`

- Assumption: this package is a compatibility shell, not a full runtime.
- Gap: `providers` is not exported even though downstream modules import `brain.runtime.providers` directly. It works because Python resolves the submodule, but the shim boundary is implicit rather than documented.

### `brain/runtime/config.py`

- Assumption: embedding config is intentionally environment-only.
- Gap: no validation or reload path. Bad model names silently fall through into degraded behavior.

### `brain/runtime/inference.py`

- Bug: `infer()` always raises. Anything that depends on LLM distillation is non-functional until a real runtime is wired.
- Assumption: `parse_operator_name()` is only a temporary heuristic.
- Gap: the regex only captures a single capitalized token, so multi-word names and lower-case introductions fall back immediately.
- TODO: replace with an OpenClaw-native inference adapter or remove codepaths that imply distillation is available.

### `brain/runtime/instrumentation.py`

- Assumption: JSONL file logging is sufficient for local-only operation.
- Gap: no log rotation or write error handling. A filesystem failure will surface as an application exception.

### `brain/runtime/model_roles.py`

- Bug: always returns the placeholder `"shim-memory-model"`.
- Gap: there is no role-to-model contract for ocmemog, so the copied brAIn role-based code has no meaningful routing target.
- TODO: bind memory/distillation roles to real OpenClaw model configuration.

### `brain/runtime/model_router.py`

- Assumption: provider-backed embeddings are optional.
- Gap: always returns an empty `ModelSelection`, so provider embedding is unreachable even if `BRAIN_EMBED_MODEL_PROVIDER` is set.
- TODO: route the `"embedding"` role to an actual OpenClaw provider selection.

### `brain/runtime/providers.py`

- Bug: the shim returns `{}` for embedding calls, so provider-backed embeddings never succeed.
- Gap: `probe_runtime()` correctly flags this, but the repo docs need to treat provider embeddings as unsupported today.
- TODO: implement an adapter over the host plugin/provider system.

### `brain/runtime/state_store.py`

- Assumption: this is a thin pass-through and intentionally minimal.
- Gap: no repo-local docs explained the `.ocmemog-state` layout before this review.

### `brain/runtime/storage_paths.py`

- Assumption: the repo-local default `.ocmemog-state` directory is intentional and matches the launcher script.
- Gap: this diverges from the original brAIn home-directory assumption, so migration/compatibility expectations should be explicit in docs.

### `brain/runtime/security/redaction.py`

- Assumption: redaction only targets obvious email/phone PII.
- Gap: no coverage for addresses, secrets, tokens, or structured metadata fields. "Redacted" in downstream docs should not be overstated.

### `brain/runtime/memory/__init__.py`

- Gap: export list is incomplete relative to the package contents. Several modules exist but are not surfaced here.
- Assumption: callers import modules directly instead of relying on package exports.

### `brain/runtime/memory/api.py`

- Resolved: rewritten to align with the local schema (`memory_events`, `tasks`, `knowledge`, `experiences`).
- Behavior: `record_task()` stores `task_id` in `metadata_json` and `status` as content.
- Behavior: `record_reinforcement()` now logs to `experiences` and a `memory_events` note.

### `brain/runtime/memory/artifacts.py`

- Assumption: binary artifact persistence is local-only and append-only.
- Gap: `content_hash = str(hash(content))` is process-randomized in Python, so it is not stable across runs and should not be treated as a durable hash.
- Gap: no deduplication or lookup helpers beyond raw file load.

### `brain/runtime/memory/candidate.py`

- Assumption: duplicate detection on `(source_event_id, distilled_summary)` is good enough for current use.
- Gap: metadata is stored but not normalized or validated.
- Gap: `verification_status` is marked `"verified"` whenever verification points exist, even though those points are generated heuristically and not actually verified.

### `brain/runtime/memory/context_builder.py`

- Assumption: only `knowledge` and `tasks` participate in ranked blocks, with directives/reflections returned separately.
- Gap: synthesis is appended if present, but `retrieval.py` never returns a `synthesis` bucket, so this path is dead today.
- Gap: `brain.runtime.roles.role_registry` does not exist in this repo. The import is wrapped, so it fails soft, but role-priority behavior is effectively disabled.
- TODO: add a real role registry integration or remove `role_id` from the documented contract.

### `brain/runtime/memory/distill.py`

- Assumption: heuristic summarization is the real fallback path in ocmemog because inference is shimmed.
- Gap: the module quietly swallows inference failures, so operators may assume distillation is model-backed when it is not.
- Gap: candidate confidence is derived from compression ratio rather than factual confidence, which is much weaker than the brAIn docs imply.
- TODO: wire real inference before exposing distillation as production-ready.

### `brain/runtime/memory/embedding_engine.py`

- Assumption: local hash embeddings are the default operating mode in this repo.
- Gap: the default "simple/hash" embedding is deterministic but extremely low fidelity; semantic recall quality will be weak.
- Gap: provider mode depends on two missing pieces at once: `model_router` selection and `providers.provider_execute`.
- TODO: document that `sentence-transformers` is optional for better local embeddings, and provider embeddings are not functional yet.

### `brain/runtime/memory/freshness.py`

- Assumption: only `knowledge` entries participate in freshness scanning.
- Gap: old directives/tasks/reflections are ignored even though they affect retrieval and context.
- Gap: `refresh_candidates` duplicates `advisories` exactly; there is no downstream refresh executor.

### `brain/runtime/memory/health.py`

- Resolved: vector coverage now uses `vector_embeddings` counts.
- Note: coverage now includes `knowledge`, `runbooks`, and `lessons` embeddings.

### `brain/runtime/memory/integrity.py`

- Assumption: integrity is schema-light and warning-oriented.
- Resolved: vector checks now compare `knowledge` to `vector_embeddings` (source_type=knowledge).
- Gap: duplicate promotion detection only returns the first grouped row, so it reports existence rather than cardinality.

### `brain/runtime/memory/interaction_memory.py`

- Assumption: interaction history is intentionally separate from the main memory DB.
- Gap: there is no foreign-key or existence guard for `person_id`.
- Gap: sentiment/outcome are stored verbatim without redaction, unlike parts of the main memory pipeline.

### `brain/runtime/memory/memory_consolidation.py`

- Assumption: this is a lightweight placeholder for future clustering.
- Gap: clustering is just `(classified_type, first_32_chars)`, which is too brittle for real consolidation.
- Gap: there is no persistence; the output only exists in-memory per call.

### `brain/runtime/memory/memory_gate.py`

- Assumption: this is intended for future prompt-routing or agent-decision logic.
- Gap: no caller in this repo uses it, so the thresholds are not operational behavior.
- Gap: output score mixes heterogeneous terms without normalization, so future consumers should treat it as heuristic only.

### `brain/runtime/memory/memory_graph.py`

- Assumption: graph edges belong in a dedicated SQLite file.
- Gap: it duplicates relationship storage already partially represented by `memory_links`, without a clear separation of responsibility.
- Gap: no current sidecar endpoint exposes or consumes this graph.

### `brain/runtime/memory/memory_links.py`

- Assumption: link creation is append-only and local.
- Gap: no uniqueness constraint, so repeated calls can create duplicate links.
- Gap: links are only fetched by exact `source_reference`; there is no reverse lookup.

### `brain/runtime/memory/memory_salience.py`

- Assumption: salience is computed from caller-provided feature scores.
- Gap: `scan_salient_memories()` feeds only freshness into the scorer, so most of the salience model is unused in practice.
- Gap: no returned item identifies which memory triggered attention.

### `brain/runtime/memory/memory_synthesis.py`

- Assumption: synthesized summaries are derived from reinforcement aggregates, not memory content.
- Gap: `list_recent_experiences()` groups by `experience_type`, so synthesis is just count summaries and does not match the richer brAIn architecture description.
- Gap: nothing injects this output into sidecar responses today.

### `brain/runtime/memory/memory_taxonomy.py`

- Assumption: taxonomy is heuristic and intentionally small.
- Gap: classification is keyword-based and can easily mislabel content.
- Gap: no persisted taxonomy field is written back to stored memories.

### `brain/runtime/memory/person_identity.py`

- Assumption: name extraction should prefer local heuristics when inference is unavailable.
- Gap: `resolve_interaction_person()` only trusts `metadata["name"]`, `email`, and `phone`; it does not inspect message text directly.
- Gap: confidence values are fixed constants, not evidence-based scores.
- TODO: integrate OpenClaw conversation metadata if identity resolution is meant to be reliable.

### `brain/runtime/memory/person_memory.py`

- Assumption: JSON columns are acceptable for the current scale.
- Gap: `update_person()` writes arbitrary field names directly into SQL; callers must stay trusted.
- Gap: there are no helper APIs to append aliases/emails/phones safely, so callers must replace whole JSON payloads.

### `brain/runtime/memory/pondering_engine.py`

- Bug/gap: `consolidate_memories([], ...)` is called with an empty list every time, so cluster-derived links are never produced.
- Gap: the only actionable output comes from unresolved-state summaries.
- Assumption: this is a scaffold, not a complete pondering loop.

### `brain/runtime/memory/promote.py`

- Assumption: promotion threshold `>= 0.5` is intentionally permissive for local memory capture.
- Gap: duplicate prevention only checks `promotions(source, content)`, not `candidate_id`, so repeated semantically identical promotions from different sources still split.
- Gap: reinforcement on successful promotion points to `promotion:{id}`, while retrieval reinforcement expects references tied to surfaced memories such as `knowledge:{id}`; that weakens ranking feedback.

### `brain/runtime/memory/reinforcement.py`

- Assumption: `experiences` is the sole reinforcement ledger.
- Gap: only one reinforcement row per `(task_id, memory_reference, outcome)` is allowed, which may collapse repeated successful interactions into a single event.
- Gap: experience payloads are free-form strings; no structured query helpers exist beyond aggregate counts.

### `brain/runtime/memory/retrieval.py`

- Assumption: keyword match is the primary retrieval mode; semantic search is a fallback for empty knowledge hits.
- Updated: semantic fallback now rehydrates `knowledge`, `runbooks`, and `lessons` when there are no keyword hits.
- Gap: reinforcement lookup uses a plain dict keyed by `memory_reference`, so multiple experiences for the same memory overwrite each other instead of aggregating.
- Gap: only exact substring matching gets a keyword score. Token overlap, stemming, and fuzzy matching are absent.

### `brain/runtime/memory/semantic_search.py`

- Assumption: this module is a more experimental ranking layer than `vector_index.search_memory()`.
- Gap: it reads `vector_embeddings` directly but does not ensure the table exists first.
- Gap: it recomputes retrieval results just to mine reinforcement weight, which is expensive and conflates retrieval score with reinforcement.
- Gap: `freshness_info` keys only cover numeric `source_id` values; non-numeric references never get freshness.

### `brain/runtime/memory/sentiment_memory.py`

- Assumption: sentiment tracking is intentionally lightweight.
- Gap: `person_memory` is imported but never used.
- Gap: sentiment detection is keyword-only and not integrated into retrieval, ranking, or sidecar responses.

### `brain/runtime/memory/store.py`

- Assumption: this is the authoritative ocmemog memory schema.
- Gap: schema version is constant `"v1"` with no migration ledger, even though `init_db()` performs live column repair.
- Gap: the schema mixes core memory, candidate workflow, and artifact metadata in one DB without documenting ownership boundaries.
- Gap: the table set does not match `api.py`, which is the largest internal contract break in the repo.

### `brain/runtime/memory/tool_catalog.py`

- Assumption: tool metadata belongs alongside memory because it can influence future retrieval/routing.
- Gap: no code in this repo records tool metadata or usage, so the table is currently dead weight.
- Gap: `record_tool_usage()` does nothing if the tool was never registered first.

### `brain/runtime/memory/unresolved_state.py`

- Assumption: unresolved cognitive items intentionally live in a separate DB.
- Gap: there is no link back into the main memory graph or sidecar endpoints.
- Gap: all unresolved states are treated equally; there is no priority/urgency field.

### `brain/runtime/memory/vector_index.py`

- Assumption: `memory_index` is a keyword/fallback index, while `vector_embeddings` is the real embedding store.
- Gap: `index_memory()` rewrites `knowledge.content` with redacted content, which mutates the source record rather than storing redaction metadata separately.
- Updated: `insert_memory()` and `index_memory()` now embed `knowledge`, `runbooks`, and `lessons`.
- Gap: fallback search returns `memory_index` entries, not canonical knowledge references, so sidecar consumers can see references that `/memory/get` cannot resolve meaningfully.

### `ocmemog/__init__.py`

- Assumption: package root is intentionally empty.
- Gap: no version or capability metadata is surfaced for the plugin package itself.

### `ocmemog/sidecar/__init__.py`

- Assumption: sidecar package docstring is sufficient for now.
- Gap: no public API surface is declared from the package.

### `ocmemog/sidecar/app.py`

- Resolved: `_get_row()` now enforces a table allow-list.
- Gap: `/memory/get` can only fetch rows from one SQLite table and returns a TODO error for unsupported references, so linked/derived references are not really supported.
- Gap: `/memory/search` falls back to substring search on the same categories, but fallback results still claim `ok: true` even when runtime is degraded.
- Updated: `DEFAULT_CATEGORIES` includes `runbooks` and `lessons`; ensure those tables are populated intentionally.

### `ocmemog/sidecar/compat.py`

- Assumption: this is the canonical runtime-readiness probe for the current sidecar.
- Gap: it checks only a small subset of modules; other broken paths (`api.py`, missing role registry) are not reported.
- Gap: `sentence-transformers` is treated as merely optional, but it is the only path to better local embeddings without wiring providers.
- TODO: expand the probe to report missing `brain.runtime.roles`, provider routing, and the known non-functional API surface.

## Recommended next steps

- Verify vector embedding coverage and decide whether to embed `runbooks`/`lessons` too.
- Treat distillation, role-aware context, provider embeddings, and identity extraction as unsupported until the shim dependencies are replaced.
- Decide whether `runbooks` and `lessons` are real first-class memory types in ocmemog. If yes, expose them in retrieval, docs, and sidecar endpoints consistently.
