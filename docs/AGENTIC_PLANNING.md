# Agent CLI 0.82.0: plans, verification and child containment

## Release scope

This release adds optional bounded task plans to the existing Agent runtime. It preserves EventLedger, RunCoordinator, the ToolRegistry policy/approval path, ArtifactStore and the global turn wallet. Protocol 0.7.0 owns the wire contracts; Reverse Proxy 4.8.0 transports structural context and consumes host evidence. Memory remains the durable semantic-memory authority.

A model proposes work; the host assigns task UUIDs, validates dependencies and scope, controls delegation and records evidence. There is no mandatory planner call for short prompts and no second orchestration engine. Tasks can execute in the parent or through the existing child manager. Multiple independent children can run within its existing admission and concurrency limits; `plan.dispatch` submits one child at a time, rather than starting an autonomous durable queue.

## Operations

Operations use the standard `kitt_runtime` tool with `operation` and `arguments`. All require an explicit conversation, turn and security context.

| Operation | Arguments | Host behavior |
| --- | --- | --- |
| `plan.submit` | `proposal` | Validate and persist one plan for this turn; reject replacements, cycles, unknown dependencies and escaped paths |
| `plan.inspect` | `{}` | Return the latest persisted revision |
| `plan.next` | `{}` | Refresh child results and file digests; return IDs whose dependencies are verified |
| `plan.dispatch` | `task_id`, optional `enabled_tools`, `token_budget`, `timeout_seconds` | Delegate an eligible task through normal child spawning, policy, approvals and leases |
| `plan.verify` | `task_id` | Execute required registered checks through normal process policy; validate syntax; record a bounded verification attempt |
| `plan.checkpoint` | `{}` | Persist dependency readiness and verification progress |

Example proposal:

```json
{
  "operation": "plan.submit",
  "arguments": {
    "proposal": {
      "schema_version": 1,
      "objective": "Fix the parser and validate its regression",
      "tasks": [
        {
          "local_id": "parser",
          "title": "Correct the parser and add the regression case",
          "role": "IMPLEMENT",
          "depends_on": [],
          "paths": ["src/parser.py", "tests/test_parser.py"],
          "check_ids": ["python.tests"]
        },
        {
          "local_id": "review",
          "title": "Review the parser and regression for correctness",
          "role": "REVIEW",
          "depends_on": ["parser"],
          "paths": ["src/parser.py", "tests/test_parser.py"],
          "check_ids": []
        }
      ]
    }
  }
}
```

Check IDs must exist in the host's BuildDetector plan for the project. The example `python.tests` requires a detected Python test project and an available runner. Unknown or unavailable required checks fail closed. Proposals cannot supply executable argv through check IDs. Empty `IMPLEMENT`/`VERIFY` tasks require at least a concrete path or a registered check. Task titles and role labels do not prove semantic correctness; the parent still owns review of the requested goal and the artifacts.

## Bounds and lifecycle

| Concern | Bound / behavior |
| --- | --- |
| Plan payload | 32 KiB, 1–12 tasks, objective up to 2,000 characters |
| Task | Label up to 64 characters, title up to 1,000, 12 dependency labels |
| Scope | Up to 64 distinct concrete file paths and 24 distinct check IDs per plan; normal workspace/capability containment applies |
| Digest reads | At most 8 MiB per task digest, plus existing per-file filesystem bounds |
| Roles | Reuse `DISCOVER`, `ARCHITECT`, `IMPLEMENT`, `VERIFY`, `REVIEW`; role capabilities intersect parent privileges |
| Verification attempts | At most 3, further bounded by configured correction cycles |
| Delegation defaults | 2,048 tokens, 120 seconds, read/search tools; explicitly enabled tools remain subject to policy |
| Timeout | Positive finite timeout, capped by worker configuration and remaining child lease duration |
| Parent cancellation | Cancel tokens and children; reject late transitions that would overwrite terminal state |
| Abrupt parent loss | POSIX worker watchdog cancels active commands and exits after a bounded grace period |
| Approval wait | Pause active duration accounting; retain consumed calls, tokens and costs |

Task states are `PENDING`, `RUNNING`, `EXECUTED`, `VERIFIED` and `BLOCKED`. Child completion produces `EXECUTED`, not `VERIFIED`. Failed, cancelled or timed-out children block their task. After failed verification the task returns to `PENDING`, or becomes `BLOCKED` at the attempt limit. Changed file digests invalidate task verification and verified dependants. Mutation observations conservatively invalidate verified tasks.

The existing `child_timeout_seconds`/worker ceiling and per-call timeout remain authoritative. No additional `KITT_SUBAGENT_TIMEOUT_MS` variable or unbounded 30-minute default is introduced. Leaf enforcement applies to child identity as well as capability intersection, including a forged shallow depth.

## Evidence and completion

The EventLedger records `TaskPlanUpdated`, `SubagentReport`, `HostToolEvidence`, `AgentLoopCheckpoint` and `TaskPlanCheckpoint`. Child artifact references use the existing ArtifactStore. Child report status comes from the host's stored child lifecycle, not stdout declarations.

Each agent-loop model request receives a trusted host-generated `OUTPUT_CONTRACT` segment containing `HostExecutionState` and the optional plan. The Agent refreshes it before each round trip. Proxy validation requires matching conversation and turn identity, a unique trusted host segment and valid counters. A remote client remains responsible for authenticating its host; the wire `TRUSTED` label is not a cryptographic attestation.

Verification records actual exit status and explicit `PASS`, `FAIL`, `SKIPPED`, `UNAVAILABLE`, `NOT_APPLICABLE`, `CANCELLED` or `TIMED_OUT`. A missing runner is not success. Required checks use exact registered argv and working directory. Stdout such as `all tests passed` or `HOST_STATUS` has no authority. Cached passing check receipts require the current declared-path digest and support approval continuation without inventing a new successful run.

Targeted checks clear only their concrete paths. Unscoped process effects remain pending until a registered workspace check succeeds. Completion requires verified mutation evidence, all planned tasks verified and no active child from the turn. The parent receives bounded correction nudges before an unresolved completion attempt is blocked. Syntax-only verification establishes syntax of declared files, not proof of business requirements. File digests protect the declared scope; reproducible project-wide dependency/environment snapshots remain future work.

Rollback now checks the content observed after the edit before restoring a previous snapshot, using filesystem preconditions. A newer concurrent edit causes conflict rather than being overwritten. A cancelled command checks the token before process creation and kills its separate process group during execution.

## Checkpoints and observability

The Agent persists a proactive checkpoint at `ceil(agent_loop_action_budget / 2)`. The Proxy adds an earlier checkpoint to longer loop budgets while preserving the behavior of budgets 1 and 2. Checkpoints never reset the global turn budget. Repeated identical failed actions, or an alternating failure loop, trigger bounded no-progress blocking. They do not measure arbitrary semantic progress through an extra model call.

`kitt sessions --json` adds `agentic` metrics derived from the ledger: latest plan revision, task count, verified task count, decomposition depth, checkpoint count, child spawn/report counts and per-task verification iterations. These are local session summaries, not a new telemetry service. No progress ratio is fabricated from prose.

## Security and performance review

All findings below were corrected in this release. Confidence is high: each was supported by a concrete code path and a regression test or executable check.

| Severity | Location | Problem / impact | Secure fix | Validation |
| --- | --- | --- | --- | --- |
| High | `children/lifecycle.py`, `security/capabilities.py` | Concurrent admission or child identity could permit excess delegation; late completion could resurrect cancellation | Serialize admission/transitions, strip spawn capability and reject child principal; terminal guards and lease cleanup | `test_child_lifecycle_guards.py` |
| High | `children/supervision.py`, `tools/process_runner.py` | Worker/tool process could outlive abruptly lost parent or start after cancellation | Actual-parent watchdog, cancellation latch, pre-spawn checks and process-group cleanup | Real POSIX parent/worker/tool regression in `test_parent_process_guard.py` |
| High | `core/workspace_snapshot.py` | Rollback could replace a newer concurrent edit | Compare current digests and apply atomic write/delete preconditions | `test_workspace_snapshot_selective.py` |
| High | Agent host evidence / Proxy contract | Text or unrelated targeted checks could manufacture completion | Typed host facts; exact registered checks; scoped pending mutations; freshness-gated verification | Agent task-plan tests and Proxy hostile-output/cross-turn tests |
| Medium | `validation/orchestrator.py` | Missing runner, cancellation or timeout could look successful | Explicit verification statuses and required-check failures | `test_verification_status.py` |
| Medium | `core/task_plan.py` | Unbounded or foreign task proposals could amplify reads or escape scope | DAG/payload/path/check bounds; host IDs; conversation/turn ownership | `test_task_plan.py` |
| Medium | `core/execution_budget.py` | Human approval time could exhaust an active execution budget | Pause/resume duration without resetting usage | `test_execution_budget.py` |
| Medium | `llm/catalog.py`, `tests/conftest.py` | Construction/tests initiated unsolicited external network requests | Cached/builtin construction; explicit refresh; tests block real remote HTTP | Catalog regression and full suite |

The coordinator serializes state changes within the existing runtime process and uses bounded DAG traversal. This release does not implement multi-host distributed scheduling or crash-atomic transactional fan-out across child lifecycle and plan storage.

## Validation and remaining acceptance work

Local validation uses Python 3.14, the real compiled `kitt-memoryd`, the Protocol 0.7.0 SDK and the Assistant namespace package. Run the full pytest suite, compileall, the clean-room production-source guard, the CI scoped mypy list and critical ruff rules. New tests cover durable DAG replay, invalidation, unknown checks, timeout validation, concurrent admission, terminal cancellation, approval evidence and abrupt parent death with a real delayed tool mutation.

One plugin-worker test requires AF_UNIX socket creation, which is prohibited in the authoring environment. It is deselected locally only; the repository CI runs the full suite unchanged. TERM and NO_COLOR must match the intended terminal rendering tests.

The 30 requirements in [AGENTIC_RUNTIME_ACCEPTANCE.md](AGENTIC_RUNTIME_ACCEPTANCE.md) remain **PENDING** until their required authenticated provider E2E evidence exists. Unit/integration tests and release CI are not substituted for provider round trips, mutation replay across real retries, interactive Ctrl+C/next prompt, or original AuthoritySnapshot validation through live stdin/signals. Record release SHA, workflow/run ID and logs for those gates before marking them passed.

## Deliberately deferred capabilities

- Mandatory `DECOMPOSE` model stage, automatic model-price routing and a new `OrchestratorAgent`: optional plans and the existing host manager cover the useful first step without duplicate authority.
- Cross-turn automatic plan reuse: current plans belong to a turn; objective changes, scope changes and stale evidence require an explicit future continuation protocol.
- Automatic retries after timeout or uncertain side effects: require idempotency/reconciliation evidence before relaunch.
- Heartbeat/reattach across Proxy reconnects: Proxy is transport; a host lease/reconciliation design must own any resumable workers.
- Windows Job Object containment after hard parent termination: ordinary cancellation works, but the new abrupt-loss watchdog is POSIX only.
- Autonomous durable fan-out queues, distributed leases, project environment fingerprints and semantic coverage evaluation: require separate acceptance evidence and resource measurements.

These items are not advertised as completed features of 0.82.0.
