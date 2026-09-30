# Agent Engineering Architecture

K.I.T.T. Agent CLI 0.80.2 keeps model reasoning flexible while moving execution
authority, durability, recovery and measurement into deterministic host-owned
contracts.

## Runtime ownership

The Agent does not duplicate schedulers, memory engines, artifact stores or
workflow runtimes. Existing subsystem owners remain authoritative:

- `EventLedger` is the append-only execution/evidence journal.
- `RunCoordinator` owns high-level run state and delegates workspace mutation
  locking to the existing FIFO `WorkspaceCoordinator`.
- `ExecutionBudgetLedger` owns the wallet for one turn and leases bounded slices
  to retained children.
- `kitt-memoryd` remains the single durable semantic-memory authority.
- `ArtifactStore` owns exact large-output/recovery bytes.
- `CompactionService` owns conversation compaction.
- Harness Episodes, interventions and experiments own evidence-based learning.
- Plugin permissions remain authority; `PluginCapabilities` is the declarative
  export contract.

## Durable execution

Each persistent turn is projected to the EventLedger before downstream
projections are considered authoritative. `RunCoordinator` supports explicit
run states and validates transitions instead of inferring state from UI activity.

Mutating tools are fenced by workspace leases. The lease remains held through
post-edit verification and rollback, closing the race between mutation and
validation.

## Context epochs and exact recovery

Every model envelope receives a content-derived `ContextEpoch` rather than a
synthetic conversation/turn identifier. The epoch hashes the effective revisions
of:

- memory + harness context;
- repository state and selected file evidence;
- active skills/guidelines;
- tool/plugin surface;
- policy, autonomy and formatting/execution constraints;
- provider/model configuration.

The epoch is persisted in the EventLedger for replay and invalidation analysis.

Large context segments and tool outputs are stored first in ArtifactStore and
represented in model context by bounded previews plus `ContextRecoveryRef`.
Compaction stores its exact pre-compaction source as a recovery artifact and
persists a structured `CompactionCheckpoint`.

## Budgets and subagents

A turn has one global budget across model calls, input/output tokens, tool calls,
duration, cost and subagent count. A retained child receives a `BudgetLease`
from that parent wallet; the child worker caps its own runtime to the lease and
cannot create a fresh independent budget.

Child sessions persist `AgentLineage`-equivalent metadata, including parent
turn, generation, role, backend/model, context fork mode, isolation mode and
budget lease identity.

## Execution authority and saved permissions

Pending approvals persist an `ExecutionAuthoritySnapshot` containing policy,
autonomy, approval revision, sandbox profile, capabilities and executable
identity. Resume revalidates that snapshot against current authority before the
grant is consumed.

Workspace-scoped remembered approvals are represented as `SavedPermission` and
can be bound to an executable identity such as `USER:...`, `GOAL:...` or
`CHILD:...`. A rule bound to one identity cannot authorize another principal.

## Plugins

Plugin manifests may declare a typed `[capabilities]` table:

```toml
[capabilities]
tools = ["my_tool"]
hooks = ["tool.after"]
commands = ["/my-command"]
providers = []
skills = []
context_sources = []
ui_extensions = []
```

Permissions answer **may the plugin perform this class of action?** Capabilities
answer **which exports does the plugin declare?** When a capability category is
declared, in-process and worker-isolated registration rejects exports outside the
declared set.

## Workspace snapshots and rollback

Before a concrete file mutation, the runtime may capture exact pre-mutation bytes
into ArtifactStore and persist a `WorkspaceSnapshot`. If post-edit verification
fails, the snapshot is restored while the mutation lease is still held. Files
that did not exist before the mutation are removed during restoration.

## Memory evidence and durable jobs

Memory recall creates a durable `RecallTrace`. The Agent propagates the trace ID
and records `MemoryConsumptionReceipt` rows for memories actually presented to
the model. Presentation is not falsely promoted to `referenced` or
`used_for_action`.

Memory background work uses durable `MemoryJob` rows with idempotency keys,
leases, attempts and retry timestamps.

## Learning loop

Task Episodes remain the evidence boundary. At terminal outcomes K.I.T.T. records
episode efficiency and repeated-pattern learning candidates to the ledger.
Candidates are evidence only and carry `auto_apply=false`; changes to skills,
hooks, plugins or harness behavior must go through an intervention and, where
appropriate, the isolated baseline/candidate experiment service.

## Structural roles

The shared Protocol `AgentRole` contract defines `DISCOVER`, `ARCHITECT`,
`IMPLEMENT`, `VERIFY` and `REVIEW`. Each role maps to explicit capabilities,
allowed tools, mutation permission, context policy, model policy and budget
policy. Tool execution checks that policy again at the registry seam, so a
read-only role cannot mutate simply because model output requests it.

`python_compute` uses the dedicated `compute.safe` capability. It does not
inherit `process.run`, because the safe evaluator cannot import, access files,
open network connections or invoke a shell.

## Managed background processes

`kitt_runtime` exposes `process.start`, `process.read`, `process.stdin`,
`process.signal`, `process.stop` and `process.resume` in addition to
synchronous `process.run`. The manager is workspace-owned rather than turn-owned,
so a process can survive across turns without losing identity.

Start captures an `ExecutionAuthoritySnapshot`. Subsequent control operations
revalidate that original authority against current policy/autonomy/approval
revisions before acting. Output is redacted and bounded, then persisted as
`PROCESS_OUTPUT` before `process.read` can expose it; process completion is
persisted as `PROCESS_EXIT`.

## No-progress and completion

The completion guard tracks action/result fingerprints, alternating exploration
loops, prose-only mutation attempts and mutation/validation progress. A stalled
implementation receives exactly one structural forward-progress nudge. Repeating
the same no-progress behavior then fails closed instead of creating an
unbounded retry loop.

## Local learning and experiments

`kitt learn` reads canonical ledger/telemetry evidence and emits only
privacy-safe tool categories such as `Read(*.java)`, `Read(lockfile)`,
`Bash(git diff)` and `mcp__server__tool`. It detects reread/tool-output/
compaction/MCP/subagent/retry/memory/cache/context/router waste without exporting
raw command arguments.

`kitt learn experiment start <feature>`, `switch <feature> control|candidate`
and `report <feature>` create measured windows. Reports compare observed
success/validation, tokens, latency, errors, tool calls and memory consumption.
Unobserved cache/cost/provider fields remain explicitly unobserved. Even when the
candidate has measurable gains and no quality regression, promotion stays
explicit; the learning service never auto-applies it.

## Skills and public memory lifecycle

Skill discovery applies `max_roots`, `max_depth`, `max_files`,
`max_file_bytes` and `max_total_bytes` before semantic selection. Plugin
exports are likewise constrained to declared `PluginCapabilities`.

The Agent publishes `session.started`, `turn.started`, `tool.completed`,
`turn.completed` and `session.ended` as evidence digests to `kitt-memoryd`.
Memory converts those hooks into its existing durable/idempotent job pipeline;
the Agent does not persist a second semantic-memory store and recalled memory is
not submitted as fresh evidence.

## Invariants

1. No mutation bypasses host policy or capability checks.
2. No approval is resumed under stale authority.
3. No subagent can mint resources outside the parent budget.
4. No post-edit validation races another mutation on the same leased paths.
5. Large exact evidence is recoverable without forcing it into model context.
6. Memory retrieval is not treated as proof of memory use.
7. Learning observations do not silently rewrite runtime behavior.
8. Background process control cannot outlive or bypass the authority captured at start.
9. Skill/plugin discovery is bounded before model-visible semantic selection.
10. Public Memory lifecycle hooks carry evidence digests, not raw prompt/tool payloads.


## Structural roles and bounded extensibility

Agent roles are host-owned contracts rather than personas. `DISCOVER`,
`ARCHITECT`, `IMPLEMENT`, `VERIFY` and `REVIEW` define capabilities,
allowed tools, mutation authority, context policy, model policy and budget policy.
The tool boundary rechecks the selected role so a read-only role cannot mutate
even if the model requests it.

Skill discovery is bounded before semantic selection by configurable maximum
roots, traversal depth, file count, per-file bytes and aggregate bytes. Plugin
exports remain constrained by typed `PluginCapabilities` in both in-process
and isolated-worker execution.

## Managed processes

The compact runtime supports `process.start/read/stdin/signal/stop/resume` in
addition to synchronous `process.run`. A managed process is workspace-scoped,
has a stable identity across turns, emits durable `PROCESS_OUTPUT` and
`PROCESS_EXIT` observations, and retains a bounded redacted output buffer.
Control operations revalidate the authority snapshot captured at process start;
stale policy, autonomy, approval revision, capability or executable identity
fails closed.

## Local learning telemetry

`kitt learn` reports local evidence with privacy-safe tool signatures such as
`Read(*.java)`, `Read(lockfile)`, `Bash(git diff)` and
`mcp__server__tool`. Raw command arguments and repository paths are not exposed
through the portfolio view.

`kitt learn experiment start <feature>`, `switch <feature> control|candidate`
and `report <feature>` measure observed sessions, turns, success/validation,
tokens, latency, errors, tool calls and memory consumption when available.
Missing provider metrics remain unobserved rather than being fabricated as zero.
Candidate promotion is never automatic.

## Public Memory lifecycle evidence

The Agent sends digest-only lifecycle evidence for `session.started`,
`turn.started`, `tool.completed`, `turn.completed` and `session.ended`.
`kitt-memoryd` validates the lifecycle class, rejects memory/recall sources and
routes the evidence into the same idempotent `MemoryJob` pipeline. No second
durable memory store exists in the Agent or Reverse Proxy.
