# K.I.T.T. Agent CLI 0.84.7 — immediate TUI session transition on submit

## Symptom

On the first prompt, the full-screen TUI could remain on the initial home screen with no visible progress. The execution itself could already be connecting or starting the Assistant daemon, attaching the conversation and bootstrapping the automatic durable contract.

## Root cause

The TUI changed `UIState.route` from `home` to `session` only in the `TurnStarted` reducer. `TurnStarted` is emitted after `TurnEventBridge.start(...)` has established the execution authority. That makes daemon/bootstrap latency invisible even though work is in progress.

## Fix

`KittUIApp.submit(...)` now establishes the visible turn state before awaiting the bridge:

- route becomes `session`;
- active conversation is set;
- submitted user prompt is shown immediately;
- status becomes `STARTING`;
- the core task is initialized and rendered.

The optimistic user block is marked internally as pending. When the real `TurnStarted` event arrives, the reducer consumes that marker if the prompt matches and does not append a duplicate. All normal `TurnStarted` behavior remains for non-optimistic callers.

If bridge startup fails, the TUI stays on the session screen, switches to `ERROR`, marks the core task failed and shows a persistent error toast.

## Regression

A behavioral test blocks `bridge.start(...)` on purpose and verifies that the UI is already on the session route with visible prompt/status/task before the bridge returns. It then delivers `TurnStarted` and verifies that only one user prompt remains in the transcript.
