# K.I.T.T. Agent CLI 0.83.15

Released: 2026-10-05

## Scope

Fix the pre-dispatch crash reported after updating to 0.83.14:

`AttributeError: 'ChildTools' object has no attribute 'repo'`

The Agent engineering composition passed `registry.child_tools` to `TaskPlanCoordinator` even though task-plan host state requires child repository access through `ChildAgentManager.repo`.

## Runtime change

- Wire `TaskPlanCoordinator` directly to `registry.child_manager`.
- Keep `ChildTools` limited to its intended spawn-only tool adapter role.
- No Protocol or Reverse Proxy contract change.

## Validation

- Added `tests/test_task_plan_child_manager.py`, which reproduces the real registry shape where both `child_tools` and `child_manager` are present and proves host-state evaluation uses the manager repository.
- Added the regression to the release-critical suite.
