# kitt-agent-cli 0.83.17

This release adopts the useful REA integration and evidence ideas without importing a second agent runtime or reverse-engineering engine.

- Adds the disabled-by-default `kitt-rea` plugin. It registers only an already-installed local `rea mcp` executable through KITT's scoped MCP API and never downloads software implicitly.
- Adds reusable plugin-owned stdio MCP registration with bounded argv/environment validation and no shell execution.
- Extends the existing evidence record with kind, authority, confidence, coverage, limitations, producer metadata and a deterministic provenance digest. SQLite schema 11 adds one metadata column; existing evidence IDs and the EventLedger remain authoritative.
- Projects required task/mutation/child verification obligations and residual unknowns into the existing trusted `OUTPUT_CONTRACT`. Completion remains host-owned and fail-closed; no second scheduler, ledger or Protocol schema is introduced.
- Preserves stronger evidence when a weaker observation for the same episode/dimension/check arrives later.

Focused regressions cover the state migration, evidence round trip, weaker-evidence protection, obligation closure and stdio MCP registration. Repository release-critical and clean-room checks remain the broader merge gates.
