# ACP v1 stdio bridge

`kitt acp --root /absolute/workspace` launches KITT as a local ACP v1
JSON-RPC 2.0 server over stdin/stdout. Supported baseline methods:
`initialize`, `session/new`, `session/prompt` (text-only), and
`session/cancel`. Agent text is streamed via `session/update`.
The client must set `cwd` to the same configured root directory.

This bridge invokes the canonical `TurnProcessor`. It does not implement a
second agent runtime or grant new filesystem/MCP capabilities. It supports
workspace-bound `session/load` and client-mediated `session/request_permission`
with an explicit single-use allow decision; the host still issues and validates
the bound approval grant. Missing, denied or timed-out permissions fail closed.
Client-supplied MCP servers, audio and image prompts are not supported.
Terminal and direct filesystem RPCs and remote network access are not exposed
through ACP.

See the Agent Client Protocol v1 specification:
https://agentclientprotocol.com/protocol/overview

## Runtime changes

`review.preview` stages a bounded content patch; `review.diff` shows
the unified diff. `review.apply` and `review.rewind` require repo.write,
the usual host policy evaluation and integrity checks. `browser.diagnose`
provides structural diagnostics from the browser gateway already bound to
the session. It never opens a browser session, captures screenshots or
returns raw page/network/console payloads.

**Security note:** Review mutations require explicit per-file `ALLOW` authorization from the host policy. An `ASK` decision is blocked pending the normal host-managed permission workflow; the review identifier alone is not a grant. Review IDs and principal ownership persist in the conversation state. Atomic CAS state claims reject concurrent mutation attempts.

## Further integration (Agent 0.88.90)

- ACP v1 now accepts workspace-checked `session/load` and exchanges `session/request_permission` to obtain explicit `allow-once` consent; only the KITT host can mint its exact pending-action grant.
- The `browser.diagnose` runtime operation now consumes live, origin-scoped Playwright event counters and fingerprints from Reverse Proxy 5.4.26, including JS exceptions, console errors, failed requests and HTTP errors. Raw headers, bodies and console messages are not returned.
- Local review UI: `kitt review list --conversation-id ID`, `kitt review diff REVIEW_ID --conversation-id ID`, and `kitt review apply|rewind REVIEW_ID --conversation-id ID --yes`.
- The CLI write commands never bypass host policy: every actual path needs ALLOW. The `--yes` switch cannot override ASK/DENY.
- `python scripts/benchmark_review.py --iterations 100 --file-bytes 1024` reports p50/p95 and traced peak memory for a single-file local review transaction; it is not a model/token benchmark.
