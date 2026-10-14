# Agent CLI 0.87.9 — Protocol 0.13.2 lock synchronization

## Root cause
The 0.87.8 adapter imports the `KAP_VERSION_HEADER`,
`KAPNegotiationError` and `negotiate_kap_version` API from Protocol
0.13.2, but the prior `uv.lock` retained Protocol 0.13.1 at SHA
`2e38e53`. A frozen uv deployment could install an incompatible SDK.

## Fix
- Pin the `kitt-protocol` resolved Git source to
  `0b9300e418557b00e371eeb2c70ef91fbf50855d` (Protocol 0.13.2)
  without changing the declared `rev=main` dependency.
- Bump Agent to 0.87.9 in project metadata, lock and source fallback.
- No behavioral changes to the 0.87.8 adapter, approval model or SSE
  streaming; tests and production build must still pass.

## Gates
Require Agent PR Checks and Docker on the exact commit and root
`ecosystem-main` plus `ecosystem-integration` after updating release pins.
No benchmarks.
