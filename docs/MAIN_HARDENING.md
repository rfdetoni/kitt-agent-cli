# KITT main hardening — f1d5ff99

Baseline: `f1d5ff993fa283bb8c339ee9a91a23944009f0e6`.

This bundle hardens the current hybrid Code-RAG integration without replacing the
Rust search engine or introducing a vector database.

## Fixes

- Context cache is namespaced by workspace + RAG configuration.
- Index freshness is established before cache hits; explicit/working-set paths
  are refreshed immediately and unrelated repositories use a bounded refresh interval.
- Context built with a working set is not written into the generic prompt cache.
- `HybridRetrievalPipeline` and semantic document cache are reused between turns.
- Embedding requests have a total deadline across batches, reject NaN/Inf and
  validate dimensions.
- Semantic candidate embeddings use a bounded in-memory LRU keyed by provider and
  code content; query embeddings remain uncached.
- RRF and ContextSelector coalesce highly-overlapping ranges so Rust + FTS slices
  do not consume duplicate context.
- Native hit parsing tolerates malformed numeric fields and non-finite scores.
- Explicit files share the context budget instead of each independently assuming
  the full turn budget.
- Persistent `RepositoryIndex` restores the dependency graph from SQLite on open.
- FTS consistency is reconciled on index initialization.
- Parser adapter version changes force reindex even when mtime/size are unchanged.
- `RepositoryIndex.close()` waits for an active background build before closing SQLite.
- In-memory history commit failures propagate instead of being reported as success.
- `AgentLoop` now transitions `REPAIR -> VERIFY`, requiring a successful verification
  before `DONE`.

## Architectural constraints preserved

- Rust native search remains lexical primary.
- Python scanner fallback is not used by Code-RAG.
- SQLite/FTS remains the deterministic fallback.
- Semantic retrieval stays optional and bounded.
- No mandatory Python dependency and no vector DB are added.
