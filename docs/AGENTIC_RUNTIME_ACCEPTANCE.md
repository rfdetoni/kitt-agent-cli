# Agentic Runtime Acceptance Matrix

Release target: Agent CLI 0.81.0 / Protocol 0.6.0 / Memory 0.7.0.

Promotion rule: a requirement is accepted only when the referenced automated test is green on the release SHA. Documentation, implementation intent, mocks without assertions, or a previous release do not count as execution evidence.

| # | Requirement | Primary automated evidence |
|---:|---|---|
| 1 | Provider context is typed/structural rather than parsed from prompt headings | `tests/test_agent_contract_context.py`, `tests/test_kitt_reverse_proxy_compat.py` |
| 2 | Legacy textual context/tool-contract fallback is absent/fails closed | `tests/test_agent_contract_context.py`, `tests/test_kitt_reverse_proxy_compat.py` |
| 3 | conversation/turn/request/route metadata is explicit and non-empty | `tests/test_reverse_proxy_phase_session.py`, `tests/test_execution_request_route_runtime.py` |
| 4 | Sync, async and recoverable retries preserve correlation metadata | `tests/test_llm_client.py`, `tests/test_kitt_reverse_proxy_compat.py` |
| 5 | ContextEpoch changes only with effective context revisions | `tests/test_agent_engineering_contracts.py` |
| 6 | Context segments reconcile as unchanged/reconciled/replaced/invalidated | `tests/test_context_efficiency.py`, context reconciliation tests |
| 7 | Durable events are persisted before external publication | `tests/test_event_ledger_replay.py` |
| 8 | Reconnect/cursor replay does not duplicate durable events | `tests/test_event_ledger_replay.py` |
| 9 | Completed mutating tool execution is not re-executed during replay | `tests/test_event_ledger_replay.py`, tool-loop replay tests |
| 10 | Large exact evidence remains recoverable through ArtifactStore | `tests/test_long_context_lifecycle.py` |
| 11 | Compaction preserves structured working/execution state | `tests/test_long_context_lifecycle.py` |
| 12 | One global execution wallet covers model/tool/subagent activity | `tests/test_execution_budget.py` |
| 13 | Classifier/condenser/execution/validator usage is stage-attributed without separate wallets | `tests/test_execution_budget.py` |
| 14 | Child leases cannot overspend the parent wallet | `tests/test_execution_budget.py` |
| 15 | Runtime binding is conversation-scoped | `tests/test_conversation_runtime.py` |
| 16 | Docker/Podman lifecycle supports provision/pause/resume semantics | `tests/test_conversation_runtime.py` |
| 17 | Runtime binding/workspace state survives reconnect and replacement | `tests/test_conversation_runtime.py` |
| 18 | Generic ResourceCoordinator prevents conflicting mutation ownership | `tests/native/test_coordinator.py`, resource coordinator tests |
| 19 | Workspace snapshot diff/preview/restore can target an explicit subset | `tests/test_workspace_snapshot_selective.py` |
| 20 | Selective rollback does not overwrite unrelated paths | `tests/test_workspace_snapshot_selective.py` |
| 21 | Approval resume rejects stale execution authority | `tests/test_authority_snapshot.py`, approval lifecycle tests |
| 22 | Saved permissions are bound to executable identity | `tests/test_agent_engineering_contracts.py` |
| 23 | Structural roles re-check mutation/tool authority at execution seam | `tests/test_agent_engineering_contracts.py`, role/security tests |
| 24 | Managed process output/exit are durable observations | `tests/test_managed_process_lifecycle.py` |
| 25 | Managed-process control rejects stale authority | `tests/test_managed_process_lifecycle.py` |
| 26 | Third identical reread/result is stopped before another execution | `tests/test_progress_aware_completion_guard.py` |
| 27 | A/B/A/B exploration loops and deterministic capability retries are blocked | `tests/test_completion_progress_stall.py`, `tests/test_progress_aware_completion_guard.py` |
| 28 | Stalled implementation gets one forward-progress redirect, then fails closed | `tests/test_progress_guard_redirect_budget.py`, `tests/test_completion_progress_stall.py` |
| 29 | Learning telemetry does not expose secrets or auto-promote candidates | `tests/test_learn_privacy.py`, credential/security tests |
| 30 | Memory is progressive, token-budgeted and Memory-owned | Agent `tests/memory/*` plus Memory `progressive_retrieval.rs` and characterization tests |

## Release evidence

Populate only after the release SHA has completed CI:

- Agent release SHA: PENDING
- Agent CI/PR/architecture workflows: PENDING
- Memory 0.7.0 SHA and CI: PENDING
- Protocol 0.6.0 SHA and CI: validated before merge
- Ecosystem clean-install/integration run: PENDING
