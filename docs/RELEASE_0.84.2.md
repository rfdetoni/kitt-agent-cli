# K.I.T.T. Agent CLI 0.84.2 — automatic durable contracts and managed proxy lifecycle

Agent CLI 0.84.2 makes the durable Plan → Execute → Validate → Retry contract the default execution path for persisted user turns in `mode=auto`.

## Automatic contracts

Normal persisted `auto` requests now:

1. ask the planner for a bounded ordered contract;
2. persist the contract as Goal items;
3. schedule the existing GoalScheduler;
4. execute one item at a time through GoalStepExecutor;
5. validate each item and retry the same item when validation fails;
6. finish only after every item, including final integration validation, is DONE.

The wrapper lives outside TurnProcessor. Planner turns, Goal-owned execution turns, explicit `ask`/`plan` turns and `no_history` turns continue through TurnProcessor directly, so the contract cannot recursively create contracts for its own items.

The default automatic contract keeps the same capability ceiling a direct auto turn could derive. PolicyEngine, autonomy rules, approvals, path containment, process/network policy and fencing remain the effective authority.

## Approval and cancellation

Goal-owned ASK approvals now resume the same contract item after executing exactly the approved pending action. Attempts are preserved; approval does not reset the item.

The outer turn tracks the active Goal turn. Cancelling the user turn cancels the real inner TurnProcessor turn and transactionally marks the contract CANCELLED.

## Reverse Proxy logging and ownership

When the Agent starts a Reverse Proxy instance from its Reverse Proxy UI/control client, it forwards:

- `--log-level`
- `--log-content`
- the Agent log directory derived from `--log-file`
- an internal owner PID

Each managed Proxy writes to its own `reverse-proxy-<instance>.log` file instead of sharing the Agent log file.

The Agent explicitly stops only Proxy instances that it started when the TUI shuts down. Reverse Proxy 4.9.16 also watches the owner PID, so managed instances terminate if the Agent exits without a clean shutdown. Manually started Proxy instances are not owned or stopped by the Agent.

## Compatibility

The daemon path requires KITT Assistant runtime with automatic-contract support and Reverse Proxy 4.9.16 for managed logging/owner lifecycle. No KITT Protocol or Memory wire schema changes are introduced.
