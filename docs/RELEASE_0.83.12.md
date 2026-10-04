# K.I.T.T. Agent CLI 0.83.12

Released: 2026-10-04

## Scope

This patch closes the highest-value concurrency and recovery gaps from the acceptance catalogue without introducing a new scheduler, protocol version or storage layer.

## Runtime change

- Serialize active turns by `conversation_id` at the async admission boundary.
- Preserve parallel execution for different conversations.
- Release the conversation admission claim on completion or cancellation so Ctrl+C recovery remains immediate.

## Acceptance evidence

- Same-conversation turns serialize deterministically.
- Different conversations execute concurrently.
- Conflicting workspace/resource waiters acquire in FIFO order and complete without deadlock.
- Selective rollback restores only requested paths.
- Workspace snapshots reconstruct from durable ledger/artifact state and restore exact bytes.
- Large artifact query and page reads remain bounded.

## Tests

- `tests/test_turn_concurrency_acceptance.py`
- `tests/test_workspace_recovery_acceptance.py`

PR validation for the acceptance implementation passed in PR Checks run `37240776981` and Docker run `37240777022`.

No KITT Protocol schema, provider contract or second coordination mechanism is introduced.
