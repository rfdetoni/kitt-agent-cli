# K.I.T.T. Agent CLI 0.84.8 — live durable-contract progress

## Observed behavior

A persisted `mode=auto` request successfully reached the Reverse Proxy, produced a valid execution contract, and started the first contract item. The outer TUI still looked idle after contract creation because the Goal scheduler consumed the inner `TurnProcessor` events instead of forwarding them to the user-facing turn.

The same log also showed a visibility gap between `edit_strategy.selected` and `tool_loop.start`. Prompt construction performs several synchronous context-enrichment steps in that interval, including kitt-memory recall, but those steps previously had no dedicated latency telemetry.

## Changes

### Live contract progress

A bounded process-local channel now connects the existing `GoalStepExecutor` to the existing outer automatic-contract iterator.

The scheduler remains the only durable execution authority. The channel carries only ephemeral UI progress events:

- context/filter progress;
- model selection/budget progress;
- thinking start/completion;
- tool proposal/start/completion;
- edits;
- retained child-agent progress.

Nested `TurnStarted`, terminal `TurnCompleted/TurnFailed/TurnBlocked`, approvals and metrics are not forwarded through this channel. This preserves the outer turn identity, approval authority and token accounting.

The outer iterator drains progress continuously and once more immediately before a terminal contract result so the final tool/edit events are not lost to a terminal-state race.

### Visible planning

Contract planning now emits `ThinkingStarted` and `ThinkingCompleted`, so the TUI exposes work while the initial LLM contract is being generated.

### Bounded memory enrichment

Interactive memory recall remains useful context but is not execution authority. Its per-turn request budget is reduced from eight seconds to four seconds. `KittMemoryUnavailable` during prompt construction now degrades to an empty memory segment instead of failing the coding turn.

### Latency evidence

The turn log now records:

- `memory_context` latency with `ok` or `unavailable` status;
- total `prompt_build` latency before `tool_loop.start`.

This makes future pre-dispatch stalls attributable without adding verbose tracing to every context helper.

## Compatibility

No wire contract changes. Protocol, Reverse Proxy, kitt-memory service protocol and tool schemas are unchanged.
