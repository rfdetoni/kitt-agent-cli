# Agent CLI 0.86.17 — honor the slow WebChat deadline

The 2026-10-13 18:01 UTC Windows API Observatory trace sent one
`stream: true` contract call whose `kitt_meta.deadline_ms` was 240000,
even though the LLM HTTP adapter already waited up to 960 seconds.
The source was `kitt/core/turn_model.py`, which capped metadata at
240000 while the proxy correctly honored that explicit cap.

The Agent now uses `min(900000, remaining_duration_ms())` so the
actual durable execution budget remains authoritative while allowing
up to fifteen minutes, not four, for a slow WebChat agent turn.

A focused regression asserts that a 600-second execution budget
is not silently truncated at 240 seconds.

No additional LLM attempts, browser actions, approvals, or guest changes.
