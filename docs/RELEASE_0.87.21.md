# Agent CLI 0.87.21 — responsive concurrent index

- Remove the long all-files SQLite transaction from full build_or_update.
- Parse and read securely outside the shared SQLite lock, commit changed files individually.
- Recheck metadata before committing parsed content.
- Protect concurrent newly updated paths from stale-file cleanup.
- Preserve index schema, FTS safety, graph behavior and all security checks.
