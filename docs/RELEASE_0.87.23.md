# Agent CLI v0.87.23 — pluggable specialists and team messaging

- Trusted declarative AgentRegistry with Code, Creative, Investigator, Critic, Tester built-ins and external agent tables in existing plugin manifests.
- Human-friendly `kitt agents create` interactive wizard and `kitt agents edit/list/inspect/enable/disable/select` commands; TUI `/agent` menu, team/auto/run/send/inbox actions.
- Persona Markdown files remain static content verified with plugin SHA-256 trust; modifications revoke activation until re-trusted.
- Task-to-specialist selection bounded by explicit role tools and parent policy; GoalScheduler's existing independent write batches can select write-capable profiles without new scheduling.
- Scoped parent/peer family messaging persists to the existing SQLite child message repository. Pending messages are drained when a child begins/resumes a new turn. No live interruption or blanket cross-workspace messaging.
- Bump pyproject.toml, uv.lock and runtime fallback to 0.87.23. No new Python dependencies or wire/protocol changes.
- Validate: pytest -q tests/test_specialist_agents.py; python scripts/run_release_critical.py; CI PR Checks and Docker.

## Peer messaging
Child roles may send via `children.send` only when the parent delegated `child.message`. Sender and parent conversation are bound from the SQLite child record, not model arguments; messages remain scoped and are consumed at turn boundaries.
