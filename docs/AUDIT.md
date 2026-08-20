# KITT Main Audit — base `443e4f65c223e97836b48085978a651878bf94d6`

This bundle was prepared against the current `main` commit audited on 2026-08-20. It is a clean-room KITT correction set; it does not vendor, copy, or depend on RTK, ICM, or Grit.

## Findings corrected

| Severity | Area | Defect | Correction |
|---|---|---|---|
| Critical | asyncio/runtime | `TurnProcessor.arun_turn()` executes blocking `run_turn()` on the event loop | bounded thread→async queue bridge with exception/cancellation handling |
| High | children/worktrees | child payloads use execution root as durable state root | explicit `state_root` propagated from `KittRuntime` through `ChildAgentManager` |
| High | approvals/editing | structural edit approval can be retargeted after approval (TOCTOU) | canonicalize symbol id/path/hash before policy/action hash; optimistic lock after approval |
| High | Rust editor | rename/removal can persist successfully then report failure because old symbol id disappears | post-write result uses replacement hash; no old-id re-resolution; preserves file permissions |
| High | Python fallback | approximate generic symbol ranges can corrupt Java/JS/Go/Rust edits | fail closed for non-Python structural fallback; direct user toward `apply_patch` |
| High | coordination | two processes can both acquire WRITE lease due SELECT→INSERT race | `BEGIN IMMEDIATE` conflict-check/write transaction |
| High | coordination | merge lock is thread-local only | DB-backed workspace integration lease + existing process-local lock |
| High | worktrees | scoped child integration stages all worktree changes | verify/stage only declared paths; reject out-of-scope modifications |
| Medium | worktrees | child git commit depends on user git identity | ephemeral `git -c user.name/user.email` identity, no config mutation |
| Medium | child recovery | approval-resume failures do not preserve coordinator state consistently | abandon/preserve worktree on resume failure |
| Medium | memory | exact lexical bonus depends on set iteration order | deterministic normalized raw-query phrase bonus |
| Medium | memory | vectors from an obsolete encoder/dimension are silently reused | encoder/dimension-aware vector lookup and regeneration |
| Medium | memory | correction `applied_count` increments on retrieval, not application | recall becomes side-effect free; application count remains explicit |
| Medium | memory isolation | concept links can reference concepts from another workspace | endpoint ownership validation |
| Medium | process output | raw artifact can be a truncated capture but is labeled uncompressed/raw | explicit `capture_truncated` + total-byte metadata |
| Medium | output compression | artifact footer can make optimized output larger than raw | final user-visible never-worse invariant |
| Medium | process semantics | cancelled process may be reported as success | cancellation included in success/error semantics |
| Medium | performance | dependency graph loops every symbol name for every symbol | extract call-like identifiers then O(1) name lookup |
| Medium | tool UX | compact `kitt_runtime` prompt omits new native/memory operations | current operation surface documented to model |
| High | CI | `pr-checks.yml` is Bun/Node CI from another project and cannot validate KITT | Python + Rust + install-smoke CI |
| High | release | `release.yml` targets `Gitlawb/kitt` and npm | PyPI universal fallback + native KITT wheel matrix |
| High | install | native build template can drift from canonical project version | native release helper derives metadata from root `pyproject.toml` |
| Medium | repository hygiene | external RTK skill still asks model to invoke an RTK binary | remove obsolete `.kitt/skills/rtk/SKILL.md` |

## Deliberately not changed

- The Python compatibility backend remains available for unsupported platforms.
- Existing public KITT tool names and policy/capability model are preserved.
- No external RTK/ICM/Grit runtime dependency is introduced.
- No commit, branch, push, or repository setting is performed by `apply_fixes.py`.
- The current Rust engine still scans source files rather than maintaining the full persistent incremental symbol index envisioned for a later performance milestone. The worst dependency-graph O(symbol²) behavior is removed here without introducing an unvalidated large cache rewrite.

## Required validation gates

```bash
python -m compileall -q kitt tests
python packaging/verify_cleanroom.py
python -m pytest -q
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features
python packaging/build_native_release.py --out dist-native
python -m pip install --force-reinstall dist-native/*.whl
python -c "from kitt.native.bridge import NativeCodeEngine; e=NativeCodeEngine('.'); assert e.status.backend == 'rust'"
```

The environment used to assemble this bundle did not contain `cargo`/`rustc`, therefore the Rust gates must be executed by the local agent/CI before merge.
