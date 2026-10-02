# Continuous Agent Runtime

Agent CLI 0.82.3 extends KITT's existing host-owned control plane for work that
spans multiple turns or detached terminal sessions. The implementation is
KITT-native: it reuses the current SQLite stores, SafeRuntime, retained-agent
manager, queue, session tree and provider registry instead of introducing a
parallel agent engine.

## Agent family messaging and roster

Retained agents can now be addressed inside one conversation family by parent,
child id or child name. Messages use one of three delivery intents:

- \`AUTO\`: KITT resolves steering vs follow-up from current child state;
- \`STEER\`: the message is intended to affect work already running;
- \`FOLLOW_UP\`: the message is durable continuation for an idle/passivated child.

The existing child message ledger remains authoritative. Cross-conversation and
cross-workspace access still fails closed.

\`children.observe\` returns the durable child roster. A completed/retained child
can be moved to \`PASSIVATED\`, which releases live coordination resources while
preserving identity, history, artifacts, model/session association and usage.
\`children.revive\` restores the retained child and may optionally assign a new
task.

Relevant runtime operations:

- \`children.send\`
- \`children.inspect\`
- \`children.observe\`
- \`children.passivate\`
- \`children.revive\`

## Continual harness refinement

\`HarnessRefiner\` now provides a complete bounded lifecycle:

\`\`\`text
trajectory/evidence
    -> model proposal (optional)
    -> normalize and validate
    -> preview
    -> persist before-snapshot
    -> host apply
    -> after-snapshot
    -> rollback when required
\`\`\`

Only prompt, knowledge, skill and subagent harness entries are editable. A model
can propose changes but cannot directly mutate the harness. Proposals are capped
at eight edits and each content body is bounded. Session scope is the default;
workspace scope is explicit.

The immutable base system prompt is never rewritten by refinement. The
model-facing safe runtime exposes `harness.refine.prepare`,
`harness.refine.apply` and `harness.refine.rollback`; they use the existing
`memory.write`/memory-save authority path rather than a new privilege class.

## Durable schedules and heartbeats

\`PersistentWakeScheduler\` activates the existing \`scheduled_tasks\` table.
Supported wake forms are:

- one-shot absolute \`run_at\`;
- fixed \`interval_seconds\`;
- minute cadence cron using \`* * * * *\` or \`*/N * * * *\`;
- one deterministic heartbeat identity per conversation.

A fired wake does not invoke a hidden agent. It enters the existing FOLLOW_UP
queue, so the next execution remains subject to the same budgets, policies,
approvals and verification rules.

The model-facing safe runtime exposes `schedule.create`, `schedule.list`,
`schedule.cancel` and `heartbeat.set`. These operations reuse the existing
`goal.manage` capability and policy path.

## Persistent bounded program sessions

KITT does not evaluate arbitrary model-produced Python or JavaScript. The new
\`program.session.*\` operations reuse the existing declarative
\`program.execute\` interpreter plus \`RuntimeStateStore\`.

State can therefore survive a turn boundary while every nested runtime call
still passes through SafeRuntime capabilities.

Operations:

- \`program.session.execute\`
- \`program.session.get\`
- \`program.session.clear\`

## Provider parking and failover

\`ProviderCircuitPool\` keeps process-local health state for configured model
profiles. Retryable connection, timeout and rate-limit failures park the failing
profile with bounded exponential backoff.

Failover is allowed only before visible output. Once a streaming provider emits
a chunk, KITT will not replay that request through another provider. This avoids
duplicate user-visible output and repeated provider-side effects.

Credentials, endpoint trust and provider configuration retain their existing
ownership and validation rules.

## Headless JSON-RPC

\`kitt rpc\` serves newline-delimited JSON-RPC 2.0 on stdin/stdout over the same
\`KittRuntime\`.

Example:

\`\`\`json
{"jsonrpc":"2.0","id":1,"method":"agents.list","params":{}}
\`\`\`

Initial methods:

- \`agents.list\`
- \`agents.send\`
- \`agents.passivate\`
- \`agents.revive\`
- \`schedule.create\`
- \`schedule.list\`
- \`schedule.cancel\`
- \`heartbeat.set\`
- \`refine.prepare\`
- \`refine.apply\`
- \`refine.rollback\`
- \`session.export\`

Use \`kitt rpc --no-start-services\` when the caller only needs direct RPC and
does not want background schedulers/extensions started.

## Existing capabilities intentionally reused

No replacement was created for functionality already present:

- \`ConversationRuntimeRegistry\` remains the per-conversation runtime authority
  for local, Docker, Podman, Kubernetes and remote runtimes.
- \`SessionTreeRepository\` and \`HistoryService\` remain the session
  branch/resume/fork/export implementation.
- \`ProviderCatalogService\` already has Models.dev cache freshness and runtime
  model discovery; it remains the catalog authority.
- KITT Memory remains the sole durable semantic-memory authority.
- The Assistant package remains the daemon/remote-service owner; Agent does not
  duplicate its transport or OS service lifecycle.

This division keeps the Agent focused on orchestration while preserving
ecosystem ownership boundaries.
