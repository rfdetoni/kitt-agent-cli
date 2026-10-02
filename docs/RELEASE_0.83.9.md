# K.I.T.T. Agent CLI 0.83.9

Released: 2026-10-02

## Scope

This patch consolidates the existing Agent-owned execution path. It does not introduce a new state machine, service, protocol field, dependency, or cross-repository contract.

## Changes

- `TurnProcessor.run_turn`, `continue_turn`, and `resume_turn` are native authoritative entrypoints; agent engineering no longer replaces them with `MethodType`.
- `TurnToolLoopMixin._execute_tool_loop` owns completion validation directly; the completion guard no longer replaces the loop at runtime.
- `ToolRegistry.execute_tool` remains the single tool execution entrypoint and delegates to the existing registry policy/handler path while applying Agent-owned engineering hooks.
- Existing durable journal, budgets, coordination, approval authority, workspace snapshots, post-edit verification, rollback, evidence, and replay behavior are retained.
- Role-policy denial returns a valid `ToolResult` with an empty output and denial metadata.

## Correlation and lifecycle

Existing local correlation remains unchanged: `conversation_id`, `turn_id`, and `request_id` are propagated to model requests; `operation_id` and `attempt_id` remain durable tool-execution identities. No new shared `trace_id` or lifecycle field is introduced because this slice found no existing local contract that required one.

## Compatibility

- Python: 3.14+
- KITT Protocol: 0.9.0
- Reverse Proxy: 4.9.3
- Wire/schema changes: none
- New dependencies: none

## Validation

Release-critical regression coverage includes authoritative entrypoint identity, role-policy denial, completion guard behavior, replay/idempotency, cancellation/new prompt, approvals/policy, tool evidence, and completion/validation.
