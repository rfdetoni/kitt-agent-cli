# Agent CLI 0.87.7 — safe parallel retrieval and UTF-8 reference prefixes

## Corrections

- Serialize the shared `RetrievalGuard` critical section across
  `repo.batch_read` workers. Eviction, repeat counts, and invalidation now
  use one re-entrant lock; the underlying independent I/O stays concurrent.
- Decode bounded `repo.ref.read` prefixes incrementally. An incomplete UTF-8
  sequence at a byte boundary is omitted instead of becoming a false U+FFFD
  replacement character. Full-file malformed UTF-8 still uses replacement
  decoding, as before.
- Add targeted regression tests for concurrent prune/observe and multibyte
  prefix boundaries (including exact complete-file reads).

## Compatibility

No KAP/2 wire-grammar changes and no new host authority. `kap.capabilities`
continues to report frame batching, early stream termination and metadata
version negotiation as unsupported. Neither the Reverse Proxy nor Protocol
must be updated for this patch. The logged-in ChatGPT Web capture issue still
needs operator-side reproduction to be considered operationally closed.

Checks: Python unit tests and existing Agent CLI PR workflows. No benchmarks.
