# Agent CLI 0.87.6 — bounded KAP/2 host extensions

## Included

- **kap.capabilities**: host introspection of supported K2 tool operations and fixed positional schema; unsupported syntax/stream features are reported explicitly as disabled.

- **repo.batch_read**: 1–8 independent `repo.read`/`repo.list` calls, max four read workers, unique IDs and 64-KiB reply bound. Each operation remains subject to original host capabilities/policies.
- **Host-schema positional batches**: fixed `id,operation,path` order under SHA-256 digest, never model-invented field orders or unsafe actions.
- **repo.ref.create / repo.ref.read / repo.ref.write**: opaque one-hour conversation-scoped revision refs, SHA-256 compare-and-swap guarded filesystem writes, replay and cross-path rejection, writes audited by the existing host policy.
- **plan.patch**: deterministic plan-event revision IDs, optimistic revision preconditions, bounded changes to pending undelegated tasks and preserved verified/execution state.
- Tests cover invalid batch requests, missing capabilities, positional digest spoofing, stale references, CAS replay, unauthorized paths, plan cycles and stale revisions.

## Security limits and interoperability

All features are host runtime operations accessed through an existing single-action K2 T envelope; **no extra KAP/2 frame grammar or opaque authority**. Protocol 0.13.1, Proxy 5.3.1 and the existing assistant IPC do not require a breaking format migration. Refer to [KAP/2 host operations](KAP2_HOST_OPERATIONS.md).

## Not shipped

Version negotiation for arbitrary K2/n frame extensions and token-level early streaming termination require protocol/transport coordination and are not silently enabled. The live ChatGPT Web incident requires operator validation against the logged-in browser; mock regression tests do not establish a production fix. This release is not an exhaustive architectural cleanup.
