# KITT Harness Evolution

## Purpose

KITT treats agent configuration, execution evidence and self-improvement as
separate concerns. The goal is not to let the model declare that it improved;
the goal is to make changes reproducible, measurable and reversible.

## Durable layers

### Session ledger

The provider-boundary request is persisted before dispatch. Model-visible state
can be reconstructed without depending on transient prompt-builder state.

### Task Episodes

A Task Episode is one user objective with one acceptance boundary. Goal-backed
work can span multiple turns while remaining one Episode. Conversation duration
is therefore not used as a proxy for task cost or task quality.

### Evidence states

Evidence uses explicit states:

- `PRESENT`
- `WIRED`
- `EXERCISED`
- `OUTCOME_SUPPORTED`
- `MISSING`
- `UNOBSERVED`
- `NOT_APPLICABLE`

`UNOBSERVED` is never normalized to success, failure or zero.

## Harness snapshots, component snapshots and presets

A harness snapshot freezes the resolved harness/runtime facts used by a run.
A component snapshot separately records logical identities and revision digests
for runtime operations, plugins, skills and MCP servers. Comparing two component
snapshots makes configuration drift explicit.

A preset is a revisioned declarative composition. KITT stores a new revision only
when the canonical payload changes, and one revision may be active per workspace.
The default runtime preset references both the harness snapshot and component
snapshot that were materialized at startup.

Materialization receipts distinguish:

```text
requested -> resolved -> materialized
```

so a configured component is not assumed to have actually been available.

## Intervention lifecycle

Harness interventions are evidence-first changes. Each intervention records:

- at least two evidence-labelled candidate causes
- the changed asset and its owner
- a baseline
- a primary metric
- a guardrail metric
- validation evidence
- a comparison window
- a stop/revert condition

Same-window validation proves only that the change is valid. A later comparable
Task Episode is required before the intervention may be treated as
`OUTCOME_SUPPORTED`.

## Controlled experiments

The Agent CLI can run bounded baseline/candidate comparisons through
`HarnessExperimentService`.

Both arms receive the same task specification and are executed in distinct Git
worktrees created by the existing `WorkspaceCoordinator`. The harness does not
implement another worktree manager. If Git isolation is unavailable, the
experiment is blocked rather than silently falling back to a shared workspace.

The evaluator returns:

```text
primary metric
guardrail metric
validation result
evidence references
details
```

The experiment result is one of:

- `CANDIDATE_BETTER`
- `NO_CHANGE`
- `REGRESSION`
- `INVALID`
- `BLOCKED`

A controlled experiment may justify applying an intervention, but it does not by
itself prove longitudinal user outcome. Heavy batch evaluation and model-scale
benchmarking can be delegated to `kitt-ai-workers` while preserving the same
snapshot and evidence contracts.

## Learning capture

Learning capture groups completed Episodes by normalized objective and aggregates
their evidence. It emits a deterministic smallest-owner hint rather than writing
new memory automatically:

- missing/unobserved validation evidence -> `quality-gate`
- missing/unobserved task understanding -> `skill`
- repeated exercised execution mechanics -> `hook-or-plugin`
- otherwise -> `memory`

The candidate remains a proposal. Durable behavior changes should go through an
intervention and subsequent comparison.

## Episode efficiency

Efficiency is reported at the Episode boundary. KITT separates wall time from
observed active duration and keeps missing telemetry as `null`/unobserved.

The current aggregate includes:

- turn count
- wall duration
- observed active duration
- input/output tokens when observed
- provider requests
- tool calls/failures
- approval requests

This avoids converting missing measurements into zero-cost work.

## Golden replay

A named golden replay stores the fingerprint and exact durable model-request
sequence for a conversation or turn. Verification recomputes the fingerprint
from the ledger. No provider key or model invocation is required.

Golden replay is intended for regression tests around:

- prompt-prefix changes
- tool-surface changes
- routing changes
- compaction changes
- migrations that affect model-visible context

## Runtime ownership

```text
Turn execution
  -> DurableTurnJournal
     -> session ledger
     -> Task Episode
     -> evidence
     -> projections

Runtime startup
  -> harness snapshot
  -> component snapshot
  -> revisioned active preset
  -> materialization receipts

Observed repeated work
  -> learning candidate
  -> intervention
  -> controlled experiment
  -> apply or reject
  -> later comparable Episode
  -> retain / adjust / rollback
```

The Python Agent CLI owns orchestration and evidence. Rust acceleration remains
owned by `kitt-toolbox` and can implement the same stable contracts later if a
measured hot path justifies it.
