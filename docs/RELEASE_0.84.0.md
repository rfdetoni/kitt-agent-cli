# K.I.T.T. Agent CLI 0.84.0 — durable task contracts

Agent CLI 0.84.0 adds an optional Plan → Execute → Validate → Retry contract loop on top of the existing Goals scheduler. It does not introduce a second runner, task engine or permission authority.

## Contract lifecycle

A contract starts from the original user request and asks the configured planning route for at most 12 ordered items. The host accepts only a bounded schema with:

- a local id, title and execution prompt;
- explicit success criteria;
- optional relative workspace paths;
- optional host-owned verification check ids;
- dependencies on earlier items only;
- one mandatory final integration item.

The model cannot grant capabilities and cannot inject shell commands through the contract schema. Unknown fields are rejected. The host expands the final item with the union of earlier paths and registered check ids so final verification covers the integrated result.

Each scheduler tick owns exactly one attempt of the current item:

1. the existing `GoalScheduler` claims and heartbeats its lease;
2. `GoalStepExecutor` runs the item through the canonical `TurnProcessor`;
3. host-owned checks run through `VerificationOrchestrator` and the existing policy;
4. normal adversarial review remains applicable;
5. a separate read-only validation turn verifies the item with bounded evidence;
6. the scheduler commits DONE / retry / blocked state only while it still owns the lease.

The executor never marks the goal complete. The scheduler can transition a contract goal to `SUCCEEDED` only in the same transactional commit that observes every contract item as `DONE`.

## Isolation and security

Contract planning and independent validation use `TurnCommand(mode="plan", no_history=True)`. In 0.84.0, `no_history` now actually excludes conversation history, working-set context, memory/harness context and uses a per-turn isolated provider session key. This prevents a browser-backed Reverse Proxy conversation from silently reusing execution-chat state during independent validation.

Capabilities remain user-owned. Contract planning cannot increase them. Registered process checks additionally require `process.run` and must pass the existing `PolicyEngine`; DENY and ASK are never silently promoted to ALLOW. Use an autonomy profile that explicitly permits unattended commands when an unattended contract is expected to execute build/test checks.

There is no shared wire-contract change in this release. KITT Protocol, Reverse Proxy and KITT Memory remain compatible without a version bump.

## Commands

Headless planning with review before execution:

```bash
kitt -p "implement the requested change" --contract --allow write,run
```

The contract is persisted in PAUSED state. Start or resume it explicitly:

```bash
kitt --contract-resume <goal_id>
```

Skip the separate plan-confirmation step:

```bash
kitt -p "implement the requested change" --contract --contract-yes --allow write,run
```

Interactive surfaces:

```text
/contract --allow write,run <prompt>
/contract-status [goal_id]
/contract-resume <goal_id>
```

`--allow` grants only the requested capability set; it does not bypass policy or approval rules.

## Failure and recovery

An item is retried in place with accumulated verification feedback. Attempts are bounded. Exhaustion or stagnation persists the current item as BLOCKED and the goal as FAILED instead of looping forever. `--contract-resume` / `/contract-resume` resets only the current blocked item and preserves previously DONE items.

## Persistence

SQLite schema version is now 12 with `goal_contract_items`. Fresh installs use the canonical schema directly; existing v11 state upgrades transactionally to v12.
