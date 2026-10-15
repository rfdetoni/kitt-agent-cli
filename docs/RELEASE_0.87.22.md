# Agent CLI 0.87.22 — selective retrieval

- Use ready FTS5 first for ordinary lexical queries, native Rust for diagnostics, explicit paths or FTS misses.
- Avoid full in-memory graph restore after targeted file mutations; refresh changed-source adjacencies and affected referrers.
- Invalidate cached PageRank scores on each edge mutation, preserve reverse-edge index invariants.
- Version Python metadata, lock and source fallback together.
