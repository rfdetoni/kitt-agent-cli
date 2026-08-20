# KITT Native Subsystem — Clean-room provenance

This implementation was produced for KITT from behavioral requirements and architectural ideas only.

## Production-code rule

The source under `kitt/native/` and `crates/` is KITT-specific and was written independently. It does **not** import, vendor, copy, submodule, execute, or depend on the RTK, ICM, or Grit projects. Their source trees are not included in this package.

The external projects were used only to identify broad product requirements already described in the design discussion, such as:

- deterministic reduction of verbose tool output;
- persistent/retrievable agent memory and knowledge;
- isolated parallel-agent workspaces and conflict coordination.

The implementation deliberately uses KITT-owned domain names and integrates with KITT's existing `SafeRuntime`, `MemoryRepository`, `DreamingService`, `ChildAgentManager`, `ArtifactStore`, `RepositoryIndex`, policy engine, security context, and migrations.

## Prohibited during local-agent application

The applying agent MUST NOT:

- copy source code from `rtk-ai/rtk`, `rtk-ai/icm`, `rtk-ai/grit`, or derived forks;
- add those repositories as git submodules, vendored directories, runtime dependencies, build dependencies, MCP dependencies, or shell-command dependencies;
- port functions line-by-line or preserve project-specific internal identifiers;
- replace KITT's memory/history/security model with an external store.

If additional behavior is needed, implement it independently from the stated behavior and KITT's own abstractions.

## Verification

Run:

```bash
python packaging/verify_cleanroom.py
```

The script intentionally scans production sources for forbidden external-project identifiers. This is a provenance guard, not a legal similarity detector; code review remains required.
