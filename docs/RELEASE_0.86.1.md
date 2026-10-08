# KITT Agent CLI 0.86.1 — Automatic parallel subagents and agent monitor

## Automatic Goals

The durable automatic contract planner can now execute independent task items concurrently in autonomous/Allow All mode. The host selects only dependency-ready tasks with disjoint concrete file scopes; the maximum number of parallel children is four by default. Children execute in their normal isolated worktrees and sessions under the current GoalScheduler fencing lease. The goal's scheduler waits for the entire admitted batch before releasing its lease, then verifies each contract item separately with existing host checks and independent review. A child report is **not** sufficient evidence of completion.

Scoped children receive read/edit tools but **not** `run_command`, which intentionally cannot confine process filesystem access to the assigned file scope. Build and integration validation remains a parent/host responsibility. The operation `plan.dispatch_ready` also lets non-Goal task plans admit multiple disjoint ready children using a single host tool action. `plan.dispatch` remains available for individual tasks.

Explicit dependencies, file overlaps, non-autonomous autonomy profiles and missing capabilities continue using the serial scheduler path. The model response contract remains KAP/1 with one action; the host executes fan-out. Authorization, risk budget and child admission continue through ToolRegistry and worker leases.

## Agent dashboard

`Ctrl+X, A` opens the subagent monitor. The former model-setup endpoint chord that collided with it is now `Ctrl+X, E`. Asynchronous child spawn/progress/finish events are bridged into the local TUI and daemon events already use the established IPC event bridge. The monitor includes the current core turn and children in individual lanes.

## Allow All and process execution

Ordinary direct `run_command` argv (such as `["python3", "--version"]`) should execute without approval in Allow All mode. The host continues to reject shell wrappers, opaque inline interpreters without explicit approval, untrusted filesystem escapes and path-scoped child subprocesses, even in Allow All. The model must use direct `argv` lists or workspace filesystem tools. This avoids falsely treating bypassing execution restrictions as a fix for command errors.

## Validation

Focused regression checks cover simultaneous worker admission (no old 2-second throttle), disjoint dispatch and cap, approval boundary, automatic contract DAG fan-out and evidence reuse, a real TUI `Ctrl+X, A` chord and direct allow-all command execution. CI must pass before release. Live WebChat provider concurrent-session load has not been measured.
