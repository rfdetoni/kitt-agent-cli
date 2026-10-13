# Agent CLI 0.86.14 — Single-flight daemon bootstrap and prompt submission

The UI can request daemon startup from more than one concurrent action.
Before this fix, those requests could each create their own IPC client,
attach the same session, and repeatedly configure logging/reasoning/autonomy.

- Serialize daemon setup and session attach per UI bridge.
- Serialize the entire turn-start boundary, including awaits, so two
  submit callbacks cannot both dispatch the same prompt before is_active
  becomes true.
- Retain the existing daemon reconnect behavior and local fallback policy.
- Add focused concurrency regressions for three simultaneous bootstrap
  requests and two simultaneous submissions.

The observed log contained only one `send_input` and one provider call,
so there is no claim that multiple LLM requests occurred in that trace.
This change closes a demonstrable same-process race. Multiple independent
GUI clients can still attach to the same daemon legitimately.
