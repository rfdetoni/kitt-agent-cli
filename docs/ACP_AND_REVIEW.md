# ACP v1 stdio bridge

`kitt acp --root /absolute/workspace` launches KITT as a local ACP v1
JSON-RPC 2.0 server over stdin/stdout. Supported baseline methods:
`initialize`, `session/new`, `session/prompt` (text-only), and
`session/cancel`. Agent text is streamed via `session/update`.
The client must set `cwd` to the same configured root directory.

This bridge invokes the canonical `TurnProcessor`. It does not implement a
second agent runtime, does not grant filesystem/MCP access, and cannot
auto-approve mutations. Prompt turns requiring approval return `refusal`.
Client MCP server configuration, audio, images and session resume are not
advertised or supported. Terminals, filesystem RPCs and remote network access
are not exposed through ACP. For a fully interactive approval UX, use the
native KITT TUI.

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
