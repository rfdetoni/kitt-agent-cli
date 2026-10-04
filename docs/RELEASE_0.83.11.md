# K.I.T.T. Agent CLI 0.83.11

Released: 2026-10-04

## Scope

This patch is the second-pass hardening of 0.83.10. It keeps the same runtime architecture and fixes two concurrency/integrity risks found during code review.

## Changes

- Bound total producer threads independently from logical active-turn slots.
- Keep cancelled synchronous producers quarantined against a finite producer-thread budget until they actually exit.
- Preserve immediate logical prompt recovery for the common blocked-cancellation case.
- Recheck artifact blob references under a SQLite `BEGIN IMMEDIATE` writer reservation immediately before unlinking a content-addressed blob.
- Keep the 0.83.10 ownership, lease-heartbeat and shared-blob regressions.

## Regression evidence

- `tests/test_cancellation_real_stop.py::test_cancelled_producer_quarantine_remains_bounded`
- `tests/test_cancellation_real_stop.py::test_cancelled_blocked_turns_do_not_exhaust_global_prompt_capacity`
- `tests/test_artifact_store_integrity.py`

No Protocol schema, provider contract, scheduler or second coordination layer is introduced.
