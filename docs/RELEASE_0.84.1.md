# K.I.T.T. Agent CLI 0.84.1 — focused goal-step verification

Agent CLI 0.84.1 is a structural quality release over 0.84.0. It preserves the durable Plan → Execute → Validate → Retry contract and its wire behavior while removing the oversized verification responsibilities from `GoalStepExecutor`.

## Refactor

`GoalStepExecutor` now owns only turn execution, event collection and delegation to verification. Mutation-path discovery and bounded review snapshots live in `review_snapshot.py`; reviewer egress/budget/model accounting lives in `review_runtime.py`; deterministic checks, adversarial review, independent contract validation and completion-state persistence are composed by `step_verifier.py`.

The refactor does not add a runner, scheduler, permission layer or dependency. Existing GoalScheduler lease/fencing authority and the contract-completion rules from 0.84.0 remain unchanged.

## Compatibility

There is no KITT Protocol, Reverse Proxy or Memory wire change. Consumers that lock Agent CLI by immutable revision should move to the validated 0.84.1 main revision after CI promotion.
