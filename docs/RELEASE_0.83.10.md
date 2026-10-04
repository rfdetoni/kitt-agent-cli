# K.I.T.T. Agent CLI 0.83.10

Released: 2026-10-04

## Scope

This patch fixes three runtime integrity failures found during the ecosystem-wide review. It reuses the existing ArtifactStore, turn streaming bridge and coordination lease mechanisms; no second scheduler, protocol version or coordination layer is introduced.

## Changes

- Preserve content-addressed artifact blobs while any live artifact row still references the same storage path.
- Reject artifact rows that provide `turn_id` without a valid matching `conversation_id`.
- Release logical turn capacity immediately when an async consumer cancels a synchronous producer that does not stop within the cancellation window.
- Keep the global concurrent-turn admission bound at four while preventing abandoned synchronous calls from starving all future prompts.
- Renew existing RunCoordinator and ResourceCoordinator leases while a mutating tool remains active.
- Stop the lease heartbeat before releasing coordination ownership.

## Regression evidence

- `tests/test_artifact_store_integrity.py` covers shared-blob GC and turn/conversation ownership.
- `tests/test_cancellation_real_stop.py` covers four cancelled blocked turns followed by a new prompt.
- `tests/test_agent_runtime_tool_leases.py` covers lease renewal during a long-running parent mutation.

Repository release-critical and PR checks remain authoritative.
