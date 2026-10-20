# Agent CLI 0.88.20 — approval/autonomy shortcuts and feedback loops

- Approvals: `A/a` always workspace, `s/S` session, `y/Y` once, `n` deny, `N` deny all. Eager modal bindings prevent active prompt buffers from swallowing the shortcut. Actual approval still requires host/daemon acknowledgment.
- Autonomy: `A/a/1` autonomous, `S/s/2` supervised, `D/d/3` read-only. UI labels and interactive mouse hit regions reflect these shortcuts. Critical host policy remains enforced even for autonomous mode.
- When a large tool output is retained, its locator includes a bounded preview; subsequent `artifacts.read`/`hydrate`/`retrieve` outputs no longer recursively create fresh artifacts. Larger reads are truncated inline with a paged `artifacts.hydrate` hint.
- Repeating exactly the same denied tool action within the same turn blocks rather than issuing another host operation; different actions remain independently subject to host authorization.
- Telemetry includes model-round count; it is not a live latency benchmark.
- Validation: focused UI keyboard/regression and tool-output tests; full PR CI required before merge.
