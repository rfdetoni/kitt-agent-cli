# K.I.T.T. state layout

K.I.T.T. uses two intentionally different state scopes.

## Project state

When K.I.T.T. runs in a project, operational state belongs to that project and is stored in `<project>/.kitt/`. This includes history, repository index, artifacts, working-set data, project memory projections, metrics, retained-agent worktrees, durable session events, Task Episodes, evidence/projection checkpoints, runtime invariant observations, and harness evolution receipts.

A retained agent executing in a temporary worktree reuses the logical parent project's state root, so durable state remains associated with the parent workspace.

## User-global state

Only data whose authority or meaning is user-global belongs in `~/.kitt/`. Examples include global memory/preferences, trusted plugin snapshots, plugin trust decisions, reusable language-formatting baselines, and other security-sensitive authorization state that must not be controlled by repository contents.

Formatting uses a global-first hierarchy. Trusted reusable language baselines live in `~/.kitt/formatting/baselines.json`; a project stores only its formatting deltas and discovery state in `<project>/.kitt/formatting.json` and `formatting.state.json`. Successful allowlisted formatters may be remembered globally by language so another project can reuse that preference without spending model context on the same defaults. Repository-controlled formatter commands are never promoted into this global trusted baseline.

The current working directory must never be mistaken for the user's home directory, and the user's home must never replace the project root for project-scoped runtime state.


## Evidence-plane durability

Schema v6 keeps evidence in the existing project history SQLite database rather
than introducing a parallel store. Exact model requests are local project state.
Projection checkpoints are disposable accelerators: the append-only session event
stream is authoritative and can replay a projection after cache loss.

Task Episodes and harness interventions are durable project evidence. They are not
global memories and are not promoted to `~/.kitt/` automatically. Cross-project
learning must pass through the existing memory/Dreaming ownership and provenance
rules.


Revisioned harness presets, component snapshots, controlled-experiment records
and golden replay fixtures remain in the same project history database. Temporary
experiment worktrees use the coordinator-owned project worktree area and are
removed after each arm; they are not promoted to global state.
