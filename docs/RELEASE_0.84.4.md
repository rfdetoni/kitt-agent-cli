# K.I.T.T. Agent CLI 0.84.4 — durable loop hardening

## Automatic contract invariants

Persisted `mode=auto` execution still uses the existing GoalScheduler and lease-fenced contract store. The planner must now return at least two items: one executable task and one final integration item. This prevents a legal but degenerate FINAL-only contract from bypassing the intended Plan → Execute → Validate → Retry loop.

Contracts that plan HIGH or CRITICAL paths under the existing deterministic review-risk classifier receive one additional read-only semantic contract review before execution. A concrete REVISE verdict feeds back into the existing bounded planner correction loop; LOW/MEDIUM contracts do not pay for the extra model call.

## Block handling

GoalStepExecutor classifies only host-owned block messages. Repeated unchanged observations and unsatisfied host evidence are resolvable and become item-level INCOMPLETE retries. Policy/environment blocks remain terminal. GoalScheduler now commits terminal BLOCKED outcomes directly instead of converting them into infrastructure exceptions that consumed the global retry/failure budget.

Recoverable TurnFailed events likewise retry the current item.

## Code intelligence

The runtime already declared semantic LSP operations but did not dispatch them. This release wires definition, hover, semantic references, diagnostics, document outline and call hierarchy to the existing bounded one-shot LanguageServerClient. The unsupported AST-search declaration was removed rather than exposing a dead operation. Native symbol/reference operations remain unchanged.

## Context/cache telemetry

ContextEpoch, segment reconciliation, FROZEN_PREFIX and SESSION_PREFIX remain the only cache-context authority. When a provider reports cached prompt tokens through a supported usage field, the Agent records a durable ContextCacheObserved event with cached-token and planned-prefix-token counts. No prompt semantics or provider cache policy are changed.

## Validation

Focused regressions cover FINAL-only plan rejection, HIGH-risk pre-mutation plan review, terminal block commit without global retry consumption, provider cache usage parsing, and the semantic runtime operation surface. No new dependency, scheduler, memory authority or wire protocol was added.
