# Agentic Runtime Acceptance Matrix

Evidence baseline: Agent CLI **0.83.12**, Protocol **0.9.0**, Memory **0.9.1**, Reverse Proxy **4.9.4**.

This matrix preserves the 30 acceptance requirements as a catalogue, but it does not treat every row as a gate for every release. Evidence is recorded only when the named behavior was actually exercised. Unit or integration evidence is not promoted to E2E evidence, and infrastructure-dependent checks remain pending until executed against the real dependency.

## Evidence classes

- **R — release invariant:** blocks a release when the changed slice can affect the invariant.
- **I — integration:** required when the corresponding component boundary changes.
- **E — external E2E:** requires real external/runtime infrastructure; it does not block unrelated slices.
- **X — experimental/long-run:** acceptance catalogue item, not a default release gate.

Statuses describe the strongest evidence currently available:

- **VERIFIED-CI:** the requirement has a directly matching automated check executed successfully on the evidence SHA below.
- **PARTIAL:** relevant checks executed, but the full requirement asks for stronger integration/E2E evidence.
- **PENDING:** no current executed evidence was found for the complete requirement.
- **BLOCKED:** the required real external environment or credentials were not exercised.

## Executed evidence registry

| Component | Evidence SHA | Workflow / run | Environment | Result |
|---|---|---|---|---|
| Agent CLI | `d74de318fd79c7ac6a2274feb5d68e885dbfdff4` | PR Checks / `37240776981`; Docker / `37240777022` | Ubuntu + Python 3.14; Windows package smoke; real Docker runtime; concurrency/recovery acceptance slice | SUCCESS |
| Memory | `3a3e9695ce8293e07ffd4839781a850834ad34dd` | ci / `37223640525` | Ubuntu; Rust workspace tests, real `kitt-memoryd` kill/restart recovery, MSRV 1.88, audit | SUCCESS |
| Protocol | `48c71b3624e9029a35a839461fe745d55c3d9dae` | ci / `37223423740` | repository CI; generated round-trip and adversarial decoder coverage | SUCCESS |
| Reverse Proxy | `e93703ff93b5926de99f7727aae2d5a64d837724` | CI / `37054991196` | Node repository CI | SUCCESS |
| Ecosystem composition | `4fcda9355fce6de6c81ede1e888db7849ed6b828` | ecosystem-integration / `37231730878` | Ubuntu full install/release gates plus Windows/macOS portable installer smokes | SUCCESS |

The Agent evidence SHA is the runtime candidate exercised by PR Checks and pinned by the ecosystem release manifest. Later documentation/workflow-only commits do not retroactively turn unexecuted runtime behavior into evidence.

## Acceptance catalogue

| # | Requirement | Owner | Class | Current executed evidence | Stronger evidence still required | Status |
|---:|---|---|:---:|---|---|---|
| 1 | ContextEnvelope round-trip and provider lowering | protocol / agent / proxy | I | Agent critical suite: `tests/test_kitt_reverse_proxy_compat.py` | final real Agent ↔ Proxy provider E2E | PARTIAL |
| 2 | Memory segment is not lost under budget pressure | memory / agent | R | Agent critical suite: `tests/memory/test_memory_manager_shared.py::test_progressive_memory_hydrates_then_keeps_budgeted_snippet` | long-context E2E only when that path changes | VERIFIED-CI |
| 3 | Recalled memory is not relearned as new memory | memory / agent | R | Agent critical suite: `test_recalled_memory_is_presented_but_never_relearned` | real memoryd E2E when memory lifecycle changes | VERIFIED-CI |
| 4 | Extraction/Consolidation job crash/retry/lease/idempotency | memory | R | Memory CI covers dedupe/retry plus `restart_recovery.rs`, which kills `kitt-memoryd`, restarts against the same SQLite DB, reclaims the expired lease as attempt 2 and completes once | none beyond boundary-specific lifecycle changes | VERIFIED-CI |
| 5 | Event persist-before-publish | agent | R | Agent critical suite: `test_event_is_persisted_before_publisher_observes_it` | none beyond boundary integration when ledger transport changes | VERIFIED-CI |
| 6 | Reconnect with cursor without duplicate event | agent | R | Agent critical suite: `test_reconnect_cursor_and_event_id_dedupe_do_not_duplicate_events` | real reconnect E2E when transport changes | VERIFIED-CI |
| 7 | Replay of mutating tool call does not repeat side effect | agent | R | Agent critical suite: `test_completed_mutation_execution_is_replayed_from_receipt_not_reserved_again` | real mutating-tool replay after crash/recovery | VERIFIED-CI |
| 8 | Ctrl+C followed immediately by a new prompt | agent | R | Agent critical suite: `tests/test_cancellation_real_stop.py::test_ctrl_c_does_not_block_next_prompt` keeps the old worker blocked while the next turn completes | interactive TUI smoke only when TUI cancellation wiring changes | VERIFIED-CI |
| 9 | Two simultaneous prompts in the same conversation | agent | I | `tests/test_turn_concurrency_acceptance.py::test_same_conversation_turns_are_serialized` proves only one active turn per conversation while preserving cancellation release | none beyond transport-specific E2E when admission changes | VERIFIED-CI |
| 10 | Different conversations execute in parallel | agent | I | `tests/test_turn_concurrency_acceptance.py::test_different_conversations_execute_in_parallel` starts two blocked turns in distinct conversations before either is released | none beyond provider-specific E2E when scheduling changes | VERIFIED-CI |
| 11 | FIFO/resource locks without starvation/deadlock | agent | I | `tests/test_turn_concurrency_acceptance.py::test_conflicting_resource_waiters_are_fifo_without_deadlock` queues two conflicting writers and proves acquisition order plus bounded completion | broader stress only if coordinator algorithm changes | VERIFIED-CI |
| 12 | ExecPolicy rejects shell/interpreter wrapping escapes | agent | R | critical suite exercises tool policy and shell-file mutation denial | direct wrapping-escape regression | PARTIAL |
| 13 | `/autonomy allow-all` respects authority boundaries | agent | R | Agent critical suite: `tests/test_autonomy_policy.py` and approval delegation tests | interactive allow-all boundary E2E | PARTIAL |
| 14 | stdin/signal uses original AuthoritySnapshot | agent | R | delegated approval binding is covered; original signal snapshot is not directly exercised | managed-process signal regression | PENDING |
| 15 | One budget includes classifier/context/validator/condenser | agent | R | Agent critical suite: global wallet and stage roll-up tests in `tests/test_execution_budget.py` | staged provider E2E only when budgeting transport changes | VERIFIED-CI |
| 16 | Concurrent subagent wallet cannot overspend | agent | R | `test_concurrent_child_tool_and_cost_leases_cannot_overspend_parent` | none beyond integration when child execution boundary changes | VERIFIED-CI |
| 17 | Worktree isolation between parent/children | agent | I | child lifecycle/admission is covered; worktree isolation itself is not directly covered | parent + child worktree integration | PARTIAL |
| 18 | Selective rollback | agent | I | `tests/test_workspace_recovery_acceptance.py::test_selective_rollback_restores_only_requested_path` restores one captured path while leaving another later mutation untouched | none beyond mutation-boundary E2E when snapshot semantics change | VERIFIED-CI |
| 19 | Artifact exact recovery | agent | I | `tests/test_workspace_recovery_acceptance.py::test_snapshot_recovers_exact_bytes_after_service_reconstruction` rebuilds the snapshot service from durable ledger/artifact state and restores exact binary bytes | none beyond storage-backend changes | VERIFIED-CI |
| 20 | Artifact query retrieval | agent | I | `tests/test_workspace_recovery_acceptance.py::test_large_artifact_query_and_page_reads_remain_bounded` proves bounded page size and bounded query hit retrieval on a file-backed artifact | none beyond artifact query implementation changes | VERIFIED-CI |
| 21 | Compaction preserves error/exit code/path/constraint | agent | R | runtime resilience suite exercises compaction routing/fallback | explicit preservation regression for all named fields | PARTIAL |
| 22 | Stuck detector catches same action/error, alternating loop and monologue | agent | R | no directly matching current test found | deterministic progress/stall regression | PENDING |
| 23 | No-progress from absent mutation/validation | agent | R | critical suite: `tests/test_required_workspace_mutation_guard.py`, completion and verification contracts | end-to-end no-progress recovery when loop changes | VERIFIED-CI |
| 24 | Docker/Podman real provision/pause/resume | agent/runtime | E | PR Checks real Docker runtime succeeded | current-SHA real Podman execution and explicit pause/resume coverage | PARTIAL |
| 25 | Runtime state persists after container replacement | agent/runtime | E | real Docker test `test_real_container_lifecycle_replacement_persistence_and_secret_sanitization` succeeded | current-SHA Podman replacement execution | PARTIAL |
| 26 | Secret does not appear in prompt/event/log | agent / proxy / runtime | R | critical secret/credential suites plus real Docker sanitization succeeded | final cross-provider/process observability scan | PARTIAL |
| 27 | Reread detector | agent | X | previous dedicated progress-aware test no longer exists on current main | focused reread regression if detector remains a supported invariant | PENDING |
| 28 | `kitt learn` does not collect sensitive arguments | agent | X | previous learn-privacy test no longer exists on current main | sanitized telemetry evidence if learn remains enabled | PENDING |
| 29 | A/B experiment never promotes candidate without sufficient evidence | agent | X | previous learn-privacy test no longer exists on current main | explicit promotion evidence if experiment path remains enabled | PENDING |
| 30 | Reverse Proxy preserves memory/context and continuity after reconnect | agent / proxy / memory | E | compatibility suite verifies structural context, stable sessions and retry boundaries | real supported WebChat reconnect with credentials/session continuity | BLOCKED |

## Promotion rules

1. A release blocks on **R** rows only when the release changes code capable of violating that invariant.
2. **I** rows become required when their boundary is changed; they are not generic gates for unrelated changes.
3. **E** rows require the real dependency. Docker evidence is Docker evidence; it is not Podman or WebChat evidence.
4. **X** rows stay in the catalogue until the corresponding feature is removed or deliberately promoted to a release invariant.
5. Mocks, adapter-only tests and implementation intent are never called E2E.
6. Every VERIFIED-CI claim must point to an executed SHA/run in the evidence registry and a test that actually exercises the named behavior.
7. A missing or renamed historical test is not silently replaced by a claim of equivalent coverage. It remains PENDING/PARTIAL until a current check proves the behavior.
8. Do not add tests merely to fill this matrix. Add the smallest regression only when a relevant behavior can break and is not already protected.

## Current gaps

The current release-critical suite is green on the recorded Agent SHA, but the catalogue is intentionally not all green. The strongest unresolved gaps are managed-process AuthoritySnapshot signal/stdin binding, parent/child worktree isolation, compaction field preservation, progress/stuck/reread/learn paths, current-SHA Podman coverage, final cross-provider secret observability, and real WebChat reconnect continuity.

Those gaps must be addressed only by the slice that owns them or by a release whose changed behavior makes them applicable.
