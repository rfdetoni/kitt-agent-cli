# ADR 0001 — Structural Provider Transport Is Authoritative

Status: Accepted for Agent CLI 0.81.0 / Protocol 0.6.0.

## Context

KITT previously accumulated prompt-shaped compatibility helpers that could move workspace context, route and tool information through textual headings. The current runtime already has typed `ContextEnvelope`, native tool schemas and `KittRequestMetadata`, so retaining prompt parsing creates two authorities that can drift.

## Decision

Internal Agent → provider/Reverse Proxy transport uses only structural fields for context, tools and correlation. Route is validated against the closed structural route set. Legacy turn-context injection and prompt-heading parsing are removed from the Agent. A textual `Tool Contract:` reaching the Reverse Proxy adapter is a protocol error.

Conversation, turn, request and session identity are correlation metadata. They are preserved across synchronous, asynchronous and recoverable retry paths but are not inferred from natural-language prompt content.

## Consequences

Provider adapters must forward typed metadata unchanged except for protocol-owned enrichment such as the resolved route/session ID. Tests must fail if removed textual helpers are reintroduced or if retry/async forwarding drops metadata.
