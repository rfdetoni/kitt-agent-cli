# Agent CLI v0.87.26 — review/plan contract convergence

## Evidence
In 2026-10-15 12:05–12:17 logs a Windows API Observatory mission caused five plan/review model calls. Every reverse-proxy request was dispatched and contract-validated. Planner output FINAL held 3–16 independent criteria, but ContractPlanner.validate was replacing them with the union of all task criteria (49+), forcing the model reviewer to reject the generated plan and consume 90–200 s per model call.

## Changes
- Preserve FINAL's explicitly authored dependencies, verification criteria/check IDs and paths, rather than silently expanding it to every task.
- Retain existing global limits: at most 64 distinct criterion texts, 64 unique paths, 12 unique host check IDs across all tasks.
- Clarify planning instruction: task checks belong to the task, FINAL validates integration, optional stages are not unconditional MVP prerequisites.
- Add independent reviewer contract regression and optional-task FINAL-preservation regression.
- No protocol, DB migration, scheduler or permission change.

## Verification
Run `python -m pytest -q tests/test_goal_contract.py tests/test_kap2_goal_prompts.py` and the release-critical CI suite. Remote logs demonstrate proxy transport; no live Windows/Wine differential test is implied.
