# K.I.T.T. Agent CLI 0.83.7

Released: 2026-10-02

## Scope

This patch fixes retry recovery inside the existing EventLedger tool-execution receipt flow. It does not add a service, database schema, dependency, or protocol version.

## Changes

- Recover the latest persisted safe-retry attempt instead of falling back to the original reservation after interruption.
- Derive `attempt_id` deterministically from `operation_id` plus the attempt number.
- Persist the attempt number on retry and completion receipts.
- Reject completion from a stale attempt after a newer retry has been persisted.
- Keep unresolved side-effecting operations fail-closed as `UNCERTAIN`; they are never retried automatically.

## Validation target

The focused regression in `tests/test_event_ledger_replay.py` must prove that a persisted retry survives state reconstruction, stale completion is rejected, and the current attempt completes with the same durable identity. Repository release-critical gates remain authoritative.
