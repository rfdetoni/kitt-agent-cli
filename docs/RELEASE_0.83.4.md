# K.I.T.T. Agent CLI 0.83.4

## Summary

This patch removes avoidable SQLite write contention from the model-dispatch critical path.

The affected flow reached `tool_loop.start` but could stall before the first `llm.request` trace and before the Reverse Proxy received `POST /v1/chat/completions`. The durable `ModelRequestPrepared` event was immediately forcing every derived session projection to checkpoint. Each projection opened its own SQLite transaction, so write contention could multiply the configured busy wait before any provider I/O started.

## Fix

- Keep `ModelRequestPrepared` authoritative and durable in `session_events`.
- Stop forcing rebuildable projection checkpoints for every model request.
- Preserve the existing sparse projection checkpoint cadence.
- Batch all projection checkpoint upserts for one observed event into a single SQLite transaction.
- Remove the duplicate `concurrent.futures` import left in `kitt.llm.client`.

No Reverse Proxy protocol change is required. Reverse Proxy 4.9.1 already exposes the capability and OpenAI-compatible endpoints used by the Agent.

## Regression coverage

Two focused regression tests cover the repaired invariant:

1. a model request is durably replayable without an eager projection checkpoint;
2. a periodic projection checkpoint uses one transaction for all registered projections.

The existing gateway, execution-budget, event-ledger and Reverse Proxy compatibility suites remain the surrounding contract coverage.
