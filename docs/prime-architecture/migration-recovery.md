# Migration, Crash Recovery & Integrity Guide

## 1. Migration Version Strategy

- Version 10 (`prime_agent_persistence_v10`): Published baseline creating `runtime_states`, `child_messages`, `daemon_events`, and baseline goal scheduling columns.
- Version 11 (`prime_agent_hardening_v11`): Adds goal leasing (`lease_id`, `lease_expires_at`), task tracking columns (`current_task_id`, `task_started_at`), correlation IDs on child messages (`correlation_id`, `reply_to`), and indexes for scheduler claims.

## 2. Integrity Verification

- SQLite connection enables foreign keys and WAL mode.
- `PRAGMA foreign_key_check` and `PRAGMA integrity_check` executed on startup and in migration test suites.

## 3. Crash Recovery Matrix

| Crash Scenario | Recovery Mechanism | Invariant Guaranteed |
|---|---|---|
| Daemon killed with SIGKILL (`kill -9`) | Stale socket and PID detected on restart; active sessions recoverable from SQLite | No corrupted state; clean daemon restart |
| TUI abruptly disconnected | Background tasks in daemon continue executing uninterrupted; sequence IDs preserved | Reattaching restores full transcript and tasks |
| SQLite database locked (`SQLITE_BUSY`) | Immediate transaction retry with exponential backoff and timeout | No lost transactions |
| Child worker process failure/timeout | Process tree killed; child status updated to `FAILED`/`TIMED_OUT` with truthful error | Child never falsely marked as completed |
| Skill runtime exception | Error captured, sandboxed and returned as structured failure | Main host runtime remains operational |
