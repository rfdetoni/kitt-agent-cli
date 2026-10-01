# Agent state schema and recovery

The Agent owns conversation/session/evidence persistence while semantic memory remains exclusively owned by `kitt-memory`. Pre-1.0 releases may change the local schema, but supported persisted Agent schemas are upgraded transactionally instead of forcing a workspace reset.

## Current behavior

- a missing database is initialized directly from the canonical current schema;
- a database already at the current schema opens normally;
- schemas 1 through 9 have an incremental path to schema 10;
- schema 7 removes retired Agent-local memory/knowledge tables and never recreates them;
- schema 8 adds typed event-ledger provenance columns;
- schema 9 adds child budget-lease and lineage state;
- schema 10 scopes remembered approvals by workspace and executable identity;
- legacy global approval rules are intentionally invalidated during the 9 -> 10 migration because assigning them to a workspace would widen authority incorrectly;
- databases newer than the running binary fail closed and require upgrading the binary;
- `kitt doctor` reports both the discovered and supported schema versions without mutating the database.

## Recovery

Normal upgrades should not use `kitt doctor --reset-state`. Reset is a last-resort operation for corrupt/unsupported state or when the user explicitly wants a clean workspace state.

Before reset, KITT creates a consistent SQLite backup using the SQLite backup API rather than copying only the main database file. This captures committed data even when WAL mode is active. The original database, WAL and SHM files are removed only after the backup succeeds.

The migration policy deliberately preserves conversation history, event evidence and child-session state while keeping retired semantic-memory storage outside the Agent.
