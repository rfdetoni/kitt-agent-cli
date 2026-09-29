# Agent state schema and recovery

The Agent supports only its current history schema. Historical local schema migrations were intentionally removed because durable semantic memory now belongs exclusively to `kitt-memory` and the ecosystem does not promise backward compatibility with obsolete Agent state.

## Current behavior

- a missing database is initialized directly from the current schema definition;
- a database already at the current schema opens normally;
- any older or newer schema is rejected fail-closed with `kitt doctor --reset-state` guidance;
- memory tables are never created in the Agent history database.

This policy prevents retired memory/knowledge tables from being resurrected by migration code and keeps recovery deterministic.
