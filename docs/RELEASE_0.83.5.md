# K.I.T.T. Agent CLI 0.83.5

Released: 2026-10-02

## Scope

This patch aligns the Agent with KITT Protocol 0.9.0 and KITT Memory 0.9.0 while preserving the existing host-owned agent loop and the standalone kitt-memoryd authority.

## Changes

- Resolve KITT Protocol 0.9.0 from the promoted main revision.
- Extend the shared memory client with the additive Memory 0.9 controls:
  - search provenance opt-out;
  - bounded `exclude_ids`;
  - opt-in context hints;
  - timeline/get provenance control;
  - deterministic `memory.baseline.request` with optional ETag reuse.
- Explicitly disable provenance hydration in `MemoryManager.get_relevant_memories()` because Agent context construction does not consume provenance payloads. Evidence receipts and recall trace identity remain unchanged.
- Preserve the existing progressive `memory.search -> memory.get` flow and bounded snippet fallback.
- Keep KITT Memory as the only durable semantic-memory authority; no Agent-local memory database or fallback store is introduced.

## Compatibility

- Python: 3.14+
- KITT Protocol: 0.9.0
- KITT Memory: 0.9.0
- Wire envelope: protocol v1
- Reverse Proxy integration: unchanged

## Validation

Focused regressions cover forwarding the Memory 0.9 search controls, baseline/ETag requests, correlation handling and verification that progressive hydration omits unused provenance.
