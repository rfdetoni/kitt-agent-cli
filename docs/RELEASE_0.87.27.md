# Agent CLI 0.87.27 — visible and truthful planning feedback

## Evidence / root cause

Log pair 2026-10-15 12:58:12/13 shows model first-token waits of 232.7s, 121.0s and 137.9s for the first three KAP/2 plan/review turns. All three were valid. The fourth failed with `ui_timeout` after the browser reported generation stopped but fresh assistant DOM nodes/candidates remained zero. The available K2/END markers were old/echoed; recovering them would risk reusing a stale model response. No host tool was executed for the fourth call.

## Changes

- Replace static stage-only transcript entries with live elapsed stage/attempt display, including an explicit `aguardando resposta do provedor` indicator after 15 seconds.
- Contract status HUD and agent dashboard now show the current phase and wait instead of a spurious percentage while an LLM is generating a plan.
- Close old phase entries on transitions, failures and cancellation without duplicate running indicators; keep existing host-owned event transport and session policies.
- Preserve the independent `FINAL` plan/review convergence fix from 0.87.26.
- Add tests verifying 70s+ waiting, stage transitions, terminal cleanup and nested thinking events.
- No new dependencies, wire/protocol version, permissions or plan scheduler changes.

## Limitations

The logged `ui_timeout` was a real *capture failure* in the already-authenticated WebChat UI. The proxy deliberately rejects stale KAP candidates and does not replay an outgoing request without explicit user intervention; this change improves visibility and diagnosis, but does **not** claim the response was recovered or provider latency was reduced. A real browser-session reproduction is needed to fix the upstream DOM extraction safely.
