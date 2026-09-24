# Runtime operations and resilience

This document describes the Agent CLI's operator-facing diagnostics and the
runtime recovery rules that protect coding sessions from duplicate work,
truncated provider streams, and maintenance traffic competing with the
execution lane.

## Session report

```bash
kitt sessions
kitt sessions --limit 40
kitt sessions --all
kitt sessions --json
```

When the Assistant daemon is available, the command starts from its visible
session roster and enriches every row with the Agent's durable SQLite state.
Without the daemon it reads the same durable state locally.

The report includes the session status, latest turn state, age since the last
known activity, last durable turn error, and accumulated telemetry tokens.
Titles are stripped of terminal control characters before rendering. JSON mode
is intended for scripts and support bundles.

## Incident timeline

```bash
kitt incident --since 30m
kitt incident --since 2h --session <session-or-turn-id>
kitt incident --since 2026-09-24T17:00:00Z --json
```

`--since` accepts ISO-8601 timestamps or bounded relative forms using
seconds, minutes, hours, or days (`30m`, `2h`, `1d`). The reader scans the
newest local structured log files under `.kitt/logs` and `.kitt/daemon`,
plus an explicitly configured `KITT_LOG_FILE`. It reads only a bounded tail
of each candidate file and keeps noteworthy warnings, failures, retries,
recovery actions, disconnects, truncations, cancellations, and related
runtime events.

The logger already applies KITT's secret and URL sanitization before records
reach disk. The incident reader additionally removes control characters from
operator-visible fields.

## Maintenance compaction

Conversation compaction uses KITT's existing semantic router rather than a
second model-selection subsystem.

1. The `summarize` route is considered first.
2. The context route is a fallback candidate when it is distinct.
3. A candidate is skipped when it is the exact execution lane, is backed by
   the KITT browser reverse proxy, or cannot fit the compaction request and
   completion reserve in its declared context window.
4. Maintenance summaries request no reasoning and use a short timeout with
   one bounded transient retry.
5. If no independent maintenance route succeeds, KITT uses the deterministic
   compaction summary.

This keeps browser conversations and the execution model's provider cache free
from one-off maintenance prompts.

## Provider recovery

Provider retry decisions use typed provider failures.

- HTTP 429 honors the provider's `Retry-After` value, including HTTP-date
  form.
- Other transient failures use exponential delay with bounded proportional
  jitter so concurrent sessions do not synchronize their retries.
- Authentication, protocol, safety/policy, and KITT semantic contract
  failures are not treated as transient provider outages.
- Once any stream output has been emitted, the generic retry layer will not
  replay that request. A later failure is surfaced to the caller so recovery
  can occur with session-aware context instead of duplicating output or side
  effects.

Direct streaming adapters require provider-specific completion evidence:
OpenAI-compatible chat accepts `[DONE]` or a final choice reason, OpenAI
Responses accepts its completed event (or `[DONE]`), and Anthropic accepts
`message_stop` (or `[DONE]`). A connection that ends before those markers is
reported as a protocol error.

## Child admission

Child records remain durable for diagnostics and history, but terminal records
do not permanently consume spawn capacity. The historical row count is not an
admission gate. Live, queued, approval-waiting, idle, and deliberately retained
children count toward the resident limit, while concurrency remains governed
by the separate active-child limit.

No daemon protocol revision or new package dependency is required by these
operator and resilience changes.


## Child mutation coordination

Retained children use Git worktrees for filesystem isolation and a separate
KITT coordination layer for semantic/write ownership. Before a child performs a
workspace mutation, KITT acquires the required write claims atomically. A
directory claim conflicts with descendants, structural symbol edits may include
bounded dependency read claims, and failed batches leave no partial leases.

The fence is deliberately placed after policy and approval checks and
immediately before the mutation. An approval that remains pending therefore
does not reserve source files indefinitely. Once a child owns resources, a
lightweight lease keeper renews them while the child is running, queued or
waiting for an approval. Completion, cancellation, failure and shutdown release
the owner.

Conflicting writers enter a bounded durable FIFO queue. A newly arriving writer
cannot bypass an older conflicting waiter. Expired leases and stale queue
entries are garbage-collected, and a timed-out wait returns an explicit
coordination failure rather than silently racing the filesystem.

## Extension tool admission

Dynamic plugin/MCP tools are validated before registration. KITT bounds the tool
name, description and JSON schema, requires an executable handler, prevents
dynamic tools from shadowing built-ins, and prevents one extension from taking
over another extension's registered name. Path-scoped children still fail
closed for dynamic tools that are not explicitly scope-aware.

These checks complement runtime capability policy; registration validation does
not grant a tool additional authority.

## Context efficiency controls

The runtime defaults are intentionally conservative and can still be overridden
through the normal Control Center/runtime configuration path:

- `compaction_trigger_ratio = 0.75` triggers history compaction from measured
  token pressure against the selected execution model's usable input budget.
- `compaction_min_tokens = 0` may raise the minimum absolute threshold when an
  operator wants to avoid compaction on small-context sessions.
- `tool_receipt_min_tokens = 160` is the minimum already-consumed host result
  eligible for receipt replacement on stateless/local providers.
- `tool_receipt_excerpt_chars = 320` bounds the diagnostic excerpt retained in
  each receipt.
- `flow.execute` accepts `parallel` (default true) and `max_parallel`
  (1..8, default 4). Data references such as `$scan.result` and explicit
  `depends_on` arrays create dependency edges; only ready read-only steps are
  scheduled together.

Receipt compaction is deliberately disabled for browser-backed reverse-proxy
sessions because rewriting historical user messages can invalidate provider
conversation identity. Large outputs continue to use artifacts independently
of receipts.

