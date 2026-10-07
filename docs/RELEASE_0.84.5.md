# K.I.T.T. Agent CLI 0.84.5 — goal-contract loop verification ownership

## Root cause

A Goals contract item executes as a scheduler-owned inner turn. The same turn was also being subjected to the generic TaskPlan host-completion gate even when `TaskPlanCoordinator.inspect(...)` returned no plan. After mutations, that gate marked completion as blocked because mutations had no TaskPlan verification evidence.

The model then received a host message asking it to run registered verification. Since no TaskPlan existed, it attempted `plan.verify`, received `No task plan for this turn`, and spent more tool calls attempting alternate validation. Large items could reach the turn tool budget before the outer `GoalStepVerifier` ever received the final response.

## Fix

- A GOAL-owned turn with no nested TaskPlan defers completion to the existing outer `GoalStepVerifier`.
- If a real TaskPlan exists for the turn, the normal TaskPlan completion gate remains authoritative.
- Contract-step prompts explicitly state that the durable Goals contract already owns decomposition and post-turn verification, so `plan.submit/plan.verify` must not be used for the current item.
- Tool-call limits are unchanged; the fix removes duplicate verification rather than raising budgets.

## Validation

Focused regressions verify all three ownership cases: GOAL without TaskPlan defers to outer verification, USER turns retain the host gate, and GOAL turns with an actual nested TaskPlan also retain the host gate. The contract prompt regression prevents reintroduction of the invalid nested `plan.verify` instruction.
