# Agent CLI 0.87.16 — visible planning and execution phases

## Incident and evidence

The 14 October 2026 19:41–19:55 Agent and reverse-proxy logs showed
three KAP/2 plan generations and three high-risk pre-mutation reviews.
Each returned a valid model response, but the reviewer verdict was
`REVISE` (4, 5 and 1 concrete issues respectively). Every logged
`tool_loop.complete` reported `tool_calls=0` and no host task executed.
The planner intentionally caps review retries at three; it would
eventually fail without ever showing the TUI which phase was running.

The reverse proxy captured these replies correctly; no proxy change
or raised timeout is warranted for this incident.

## Fix

- Emit `ContractProgress` events at each generation, validation/review,
  revision, acceptance and plan-rejection boundary. All stages are
  emitted before their potentially long LLM call, not after it.
- The Agent CLI daemon bridge forwards these data-only events via the
  existing turn stream, and the TUI displays the precise stage and
  attempt (e.g. `PLANNING 2/3` or `REVIEWING 2/3`) in the HUD and
  a bounded system entry per stage. The live timer continues.
- Contract scheduling and execution update the HUD with completed
  item counts and the current item/status, instead of presenting
  planning or task execution as model text streaming.
- The existing synchronous `ContractPlanner.plan()` public interface
  remains supported; automatic mode uses the streaming iterator.
- No model chain-of-thought, original prompts, reviewer issue text,
  credentials or unverified percentage progress is exposed.

## Verification

The critical CI suite includes the phase-by-phase generator, three
rejected attempts, daemon event mapping and TUI state reducer tests.
The host remains authoritative for tool execution, approvals and
completion. This fix does **not** force a high-risk plan to pass its
review; if a reviewer rejects every revision, the TUI shows that
reason and the turn fails rather than silently fabricating progress.
