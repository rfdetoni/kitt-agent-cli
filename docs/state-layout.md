# K.I.T.T. state layout

K.I.T.T. uses two intentionally different state scopes.

## Project state

When K.I.T.T. runs in a project, operational state belongs to that project and is stored in `<project>/.kitt/`. This includes history, repository index, artifacts, working-set data, project memory projections, metrics, and retained-agent worktrees.

A retained agent executing in a temporary worktree reuses the logical parent project's state root, so durable state remains associated with the parent workspace.

## User-global state

Only data whose authority or meaning is user-global belongs in `~/.kitt/`. Examples include global memory/preferences, trusted plugin snapshots, plugin trust decisions, and other security-sensitive authorization state that must not be controlled by repository contents.

The current working directory must never be mistaken for the user's home directory, and the user's home must never replace the project root for project-scoped runtime state.
