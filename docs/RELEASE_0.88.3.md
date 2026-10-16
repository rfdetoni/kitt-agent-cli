# Agent CLI 0.88.3 — honor allow-all and resume concrete approved actions

The user's autonomous process permission now also applies to inline Python/Node commands. `evaluate_argv` still classifies those payloads as opaque; supervised/balanced modes require approval, read-only mode and hard denials remain enforced, and network/control-plane capability gates are unchanged.

Pending actions now persist the concrete tool and arguments supplied by host approval metadata. The previous descriptor mutated a handler-local copy, so process.run was persisted as kitt_runtime and failed scheduler fencing on approval. The unused argument-mutation descriptor is removed. Mutation preconditions follow the concrete action; patches retain the host's original hashes.

Contract Goal resumption belongs to resolve_goal_approval after execution and evidence persistence. ToolRegistry no longer also resumes contract Goals, avoiding a scheduler race and a false second-resume error after a successful command. Plain fallback routes approvals through the same contract owner. Legacy non-contract Goal and child continuation behavior is retained.

Regressions exercise model JSON -> SafeRuntime -> durable pending action -> exact grant -> real command -> leased Goal resumption. They use the logged `python3 -c` spelling, all approval scopes, single execution, replay rejection, and composite patch preconditions. The previous main reproduces the modal and lease failures. No new dependency or shared wire schema.
