# Agent CLI 0.87.18 — incremental index performance

- Index qualified symbol names, per-file chunks and symbol start positions.
- Remove FTS5 entries by rowid before deleting backing chunks.
- Read full contents in full reconciliation only when metadata changes.
- Rely on atomic SQLite updates; keep full FTS consistency repair on startup instead of on every changed path.
- Align package version and editable uv.lock entry at 0.87.18.

The external protocol and authorization remain unchanged.