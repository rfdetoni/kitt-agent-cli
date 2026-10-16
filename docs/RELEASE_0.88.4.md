# Agent CLI 0.88.4 — reliable approval lifecycle and parallel scheduling

- Persist pending approvals before exposing them in memory. A repeated request is idempotent only while still pending and bound to the same tool/action; decided requests cannot be silently reopened or rebound after a restart.
- Resume the exact effective tool and arguments chosen by the host, retaining the original before-hashes for composite patches. The 0.88.3 allow-all and Goal fencing/lease behavior remains authoritative.
- Retire approvals when their turn is cancelled and report request collisions as structured blocked results.
- Guarantee that the scheduler-owned contract item is admitted in bounded parallel batches, and reject overlapping paths including parent directories, ambiguous path scopes and traversals.
- Extract action-bound approval creation into small, single-purpose helpers. Keep the test suite focused on durable authority, conflict rollback, exact execution and scheduling regressions.
- No dependency or shared wire schema changes. Release verification uses the current validated Assistant companion commit.

Validation: run the release-critical suite, complete pytest suite, Python compilation, static checks, clean-room guard, and Docker/Podman/Windows smoke tests.
