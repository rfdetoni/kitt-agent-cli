# Agent CLI 0.87.8 — KAP/2 HTTP version negotiation

## Changes

- With an explicitly enabled `X-Kitt-Agent-Contract` request, the native
  Reverse Proxy adapter sends the exact `X-Kitt-KAP: KAP/2` version.
  Malformed or conflicting user-supplied selections are refused without I/O.
- Real HTTP responses must acknowledge `X-Kitt-KAP: KAP/2`; missing or
  incompatible versions produce `ProviderProtocolError`. This avoids
  silent downgrade and preserves single-action host authority.
- Requests without the Agent contract leave regular providers unchanged.
- Targeted tests exercise exact header transmission, acknowledgement mismatch
  and rejection before submission.
- Depends on Protocol 0.13.2 and Proxy 5.3.2.

## Boundaries

- Header negotiation is transport metadata; it does not enable syntax
  `K2/n`, bypass permissions or permit client-side tool execution.
- Proxy's verified early *logical* completion uses assistant-owned DOM
  provenance and pretrusted CDP. The Agent still consumes the complete
  SSE response, including `[DONE]`; no early client connection abort.
- No benchmarks. Real authenticated browser acceptance remains distinct
  from CI validation.
