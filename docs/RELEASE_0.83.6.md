# K.I.T.T. Agent CLI 0.83.6

Released: 2026-10-02

## Scope

This patch hardens tool replay/idempotency without adding a new persistence service or protocol version. The existing EventLedger remains the durable authority for tool execution receipts.

## Changes

- Keep the existing deterministic `execution_id` as the logical operation identity and expose it explicitly as `operation_id`.
- Add a durable `attempt_id` per execution attempt.
- Preserve confirmed side effects as replay-only: the same logical operation returns its stored receipt instead of executing again.
- Preserve fail-closed recovery for unresolved side effects: a reserved side-effecting operation without completion remains uncertain and is not retried automatically.
- Allow only non-side-effecting unresolved operations to retry automatically; each retry keeps the same `operation_id` and receives a new `attempt_id`.
- Record explicit receipt outcomes for confirmed success, known failure, pending safe retry, and uncertain side-effect recovery.

## Compatibility

- Python: 3.14+
- KITT Protocol: 0.9.0
- KITT Memory: 0.9.0
- Wire envelope: unchanged
- No new database schema or external dependency

## Validation

Focused regressions cover:

- persist-before-publish and reconnect cursor behavior;
- confirmed mutation replay without a second real side effect;
- retry identity for a safe non-side-effecting operation;
- known failure outcome with the final `attempt_id`.

The broader repository gates remain the release authority. Evidence is recorded only after those workflows execute on the final candidate SHA.
