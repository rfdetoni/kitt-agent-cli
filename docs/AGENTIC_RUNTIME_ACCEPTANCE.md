# Agentic Runtime Acceptance Matrix

Release target: Agent CLI 0.81.0 / Protocol 0.6.0 / Memory 0.7.0.

This matrix mirrors the **30 original mandatory acceptance tests** from the architectural completion prompt 1:1. A row is PASS only with executed evidence on the final release SHA. Documentation, implementation intent, fake-only runtime tests, adapter-only proxy tests, or a previous release are not acceptance evidence.

| # | Requirement | Owner repo | Unit test | Integration test | Real-runtime / E2E evidence | Release SHA | Workflow / run | Status |
|---:|---|---|---|---|---|---|---|---|
| 1 | ContextEnvelope round-trip and provider lowering | protocol / agent / proxy | `tests/test_agent_contract_context.py` | `tests/test_kitt_reverse_proxy_compat.py` | final Agent ↔ Proxy E2E | PENDING | PENDING | PENDING |
| 2 | Memory segment is not lost under budget pressure | memory / agent | `tests/memory/test_memory_manager_shared.py` | Memory progressive retrieval suite | long-context E2E | PENDING | PENDING | PENDING |
| 3 | Recalled memory is not relearned as new memory | memory / agent | `tests/memory/test_memory_manager_shared.py` | Memory receipt/lifecycle integration | Agent + memoryd E2E | PENDING | PENDING | PENDING |
| 4 | Extraction/Consolidation job crash/retry/lease/idempotency | memory | Memory durable-job tests | Memory crash/retry integration | real memoryd restart/recovery | PENDING | PENDING | PENDING |
| 5 | Event persist-before-publish | agent | `tests/test_event_ledger_replay.py` | durable event integration | long-run E2E | PENDING | PENDING | PENDING |
| 6 | Reconnect with cursor without duplicate event | agent | `tests/test_event_ledger_replay.py` | reconnect replay integration | long-run reconnect E2E | PENDING | PENDING | PENDING |
| 7 | Replay of mutating tool call does not repeat side effect | agent | `tests/test_event_ledger_replay.py` | tool replay/idempotency integration | replay E2E | PENDING | PENDING | PENDING |
| 8 | Ctrl+C followed immediately by a new prompt | agent | `tests/test_cancellation_unblocks_prompt.py` | TUI cancellation integration | long-run Ctrl+C E2E | PENDING | PENDING | PENDING |
| 9 | Two simultaneous prompts in the same conversation | agent | `tests/test_concurrency.py` | run-coordinator integration | concurrency E2E | PENDING | PENDING | PENDING |
| 10 | Different conversations execute in parallel | agent | `tests/test_concurrency.py` | run-coordinator integration | concurrency E2E | PENDING | PENDING | PENDING |
| 11 | FIFO/resource locks without starvation/deadlock | agent | `tests/test_resource_coordinator.py` | `tests/native/test_coordinator.py` | concurrent resource E2E | PENDING | PENDING | PENDING |
| 12 | ExecPolicy rejects shell/interpreter wrapping escapes | agent | `tests/test_execution_sandbox.py` | `tests/prime_architecture/test_safe_runtime_security.py` | real process runtime | PENDING | PENDING | PENDING |
| 13 | `/autonomy allow-all` respects authority boundaries | agent | `tests/test_autonomy_policy.py` | approval/policy integration | interactive approval E2E | PENDING | PENDING | PENDING |
| 14 | stdin/signal uses original AuthoritySnapshot | agent | `tests/test_authority_snapshot.py` | `tests/test_managed_process_lifecycle.py` | managed-process E2E | PENDING | PENDING | PENDING |
| 15 | One budget includes classifier/context/validator/condenser | agent | `tests/test_execution_budget.py` | actual staged-call budget integration | long-run budget evidence | PENDING | PENDING | PENDING |
| 16 | Concurrent subagent wallet cannot overspend | agent | `tests/test_execution_budget.py` | child-agent budget integration | subagent E2E | PENDING | PENDING | PENDING |
| 17 | Worktree isolation between parent/children | agent | retained-agent lifecycle tests | child/worktree integration | parent + two-child E2E | PENDING | PENDING | PENDING |
| 18 | Selective rollback | agent | `tests/test_workspace_snapshot_selective.py` | snapshot integration | rollback E2E | PENDING | PENDING | PENDING |
| 19 | Artifact exact recovery | agent | `tests/test_long_context_lifecycle.py` | artifact-store integration | long-artifact recovery E2E | PENDING | PENDING | PENDING |
| 20 | Artifact query retrieval | agent | artifact-store tests | artifact query integration | long-artifact recovery E2E | PENDING | PENDING | PENDING |
| 21 | Compaction preserves error/exit code/path/constraint | agent | `tests/test_long_context_lifecycle.py` | compaction invariant integration | long-context E2E | PENDING | PENDING | PENDING |
| 22 | Stuck detector catches same action/error, alternating loop and monologue | agent | `tests/test_completion_progress_stall.py` | progress-guard integration | stalled-agent E2E | PENDING | PENDING | PENDING |
| 23 | No-progress from absent mutation/validation | agent | `tests/test_progress_guard_redirect_budget.py` | completion guard integration | no-progress E2E | PENDING | PENDING | PENDING |
| 24 | Docker/Podman real provision/pause/resume | agent/runtime | `tests/test_conversation_runtime.py` | `tests/integration/test_real_container_runtime.py` | `runtime-integration.yml` Docker + Podman | PENDING | PENDING | PENDING |
| 25 | Runtime state persists after container replacement | agent/runtime | `tests/test_conversation_runtime.py` | `tests/integration/test_real_container_runtime.py` | real replacement workflow | PENDING | PENDING | PENDING |
| 26 | Secret does not appear in prompt/event/log | agent / proxy / runtime | credential/security tests | redaction integration | final secrets scan | PENDING | PENDING | PENDING |
| 27 | Reread detector | agent | `tests/test_progress_aware_completion_guard.py` | progress-guard integration | long-run E2E | PENDING | PENDING | PENDING |
| 28 | `kitt learn` does not collect sensitive arguments | agent | `tests/test_learn_privacy.py` | learn telemetry integration | sanitized telemetry evidence | PENDING | PENDING | PENDING |
| 29 | A/B experiment never promotes candidate without sufficient evidence | agent | `tests/test_learn_privacy.py` | learn experiment integration | explicit-promotion evidence | PENDING | PENDING | PENDING |
| 30 | Reverse Proxy preserves memory/context and continuity after reconnect | agent / proxy / memory | proxy contract tests | Agent ↔ Proxy reconnect suite | supported real WebChat E2E | PENDING | PENDING | PENDING |

## Promotion rules

- Every row must identify the **final release SHA** and concrete workflow/run.
- Tests #24 and #25 require real Docker and Podman; fake drivers are unit evidence only.
- Test #30 requires a real supported WebChat flow; mock HTTP alone is insufficient.
- Test #26 scans provider prompt, ContextEnvelope, EventLedger, process events, persisted runtime config, artifacts, review telemetry, logs and exceptions.
- Test #15 executes the stages; the existence of `stages` fields in a snapshot is not sufficient.
- Missing infrastructure or evidence remains **PENDING/BLOCKED**, never silently promoted to PASS.

## Release evidence

- Agent release SHA: PENDING
- Protocol 0.6.0 SHA/tag/release: PENDING FINAL VERIFICATION
- Memory 0.7.0 SHA/tag/release: PENDING FINAL VERIFICATION
- Reverse Proxy compatibility SHA: PENDING FINAL VERIFICATION
- Ecosystem clean-install/integration run: PENDING
- Long Agent + Reverse Proxy + Memory + Runtime E2E: PENDING
