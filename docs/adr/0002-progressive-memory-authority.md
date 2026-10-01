# ADR 0002 — Progressive Retrieval Remains Memory-Owned

Status: Accepted for Agent CLI 0.81.0 / Memory 0.7.0 / Protocol 0.6.0.

## Context

A fixed `memory.recall(limit=8)` call couples retrieval quality to an arbitrary count and forces the Agent to decide how much durable memory to hydrate. KITT Memory already owns scope, sensitivity, ranking, provenance and recall traces, so token pressure belongs at that boundary.

## Decision

The normal Agent read path is `memory.search` followed by selective `memory.get`; `memory.timeline` is available when chronology/source continuity is required. Every operation carries an explicit token budget. Search returns bounded snippets/provenance first; get hydrates only selected IDs and reports IDs truncated by budget.

The Agent may retain a returned search snippet when the full record does not fit, but it does not create a parallel semantic-memory store or reproduce Memory ranking/authorization logic. Legacy recall remains only a transitional/internal primitive during coordinated upgrades.

## Consequences

KITT Memory remains the sole durable semantic-memory authority. Protocol owns the cross-language request/response DTOs. Agent tests verify budget forwarding and progressive hydration; Memory tests verify scope, sensitivity, provenance and token-bounded responses.
