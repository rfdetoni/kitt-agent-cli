# Implementation manifest

## Added production modules

### Rust

- `crates/kitt-native-engine/src/model.rs` — stable data contracts and lease-conflict primitive.
- `crates/kitt-native-engine/src/language.rs` — KITT Tree-sitter language registry.
- `crates/kitt-native-engine/src/search.rs` — bounded repository search.
- `crates/kitt-native-engine/src/symbols.rs` — symbols, reads, references and dependency graph.
- `crates/kitt-native-engine/src/edit.rs` — optimistic structural replacement with syntax validation and atomic write.
- `crates/kitt-native-engine/src/output.rs` — deterministic command-family output reduction.
- `crates/kitt-native-engine/src/lib.rs` — pure Rust public API.
- `crates/kitt-native-python/src/lib.rs` — ABI3 PyO3 bindings only.

### Python

- `kitt/native/bridge.py` — native-first/fallback selector.
- `kitt/native/fallback.py` — zero-dependency compatibility implementation.
- `kitt/native/storage.py` — extension schema and persistence API.
- `kitt/native/memory.py` — hybrid memory, concepts and corrections.
- `kitt/native/output.py` — output optimization + raw ArtifactStore retention.
- `kitt/native/coordinator.py` — worktrees, leases and merge gate.
- `kitt/native/runtime.py` — composition object.

## Existing KITT integration performed by `apply_cleanroom.py`

- `kitt/core/runtime.py`
  - separates execution/state roots;
  - builds `NativeSubsystem`;
  - wraps existing `MemoryManager` rather than replacing Dreaming;
  - attaches engine/output/coordinator to existing registry/children;
  - subscribes to Dream events.
- `kitt/children/worker.py`
  - consumes `state_root` while executing from child worktree.
- `kitt/children/manager.py`
  - prepares/reuses worktree;
  - includes roots on run and approval continuation;
  - integrates successful child edits;
  - preserves failed/cancelled work for recovery.
- `kitt/tools/handlers/search.py`
  - native search fast path with existing index/scanner fallback.
- `kitt/tools/handlers/system.py`
  - path leases for patch edits;
  - command output optimizer and raw artifact retention.
- `kitt/runtime/safe_runtime.py`
  - native search/inspect/read/reference/edit operations;
  - memory correction/concept/link operations behind existing capability/policy model.
- `kitt/tools/registry.py`
  - advertises `memory.*` as part of compact `kitt_runtime` family.
- `kitt/history/database.py`, `kitt/history/migrations.py`
  - schema version 14 and native extension tables.

## Validation included

- Python fallback unit tests.
- lease-conflict tests.
- hybrid memory and knowledge tests.
- structural-edit stale-hash test.
- never-worse output test.
- clean-room source guard.
- GitHub Actions Rust fmt/clippy/test gate.
- native wheel matrix skeleton.
- local benchmark utility.
