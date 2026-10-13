# Agent CLI 0.86.16 — Correct MCP stdio read buffer

On 2026-10-13, the Windows API Observatory workspace recorded three
failed REA MCP reader loops: "Separator is not found, and chunk exceed
the limit". The daemon could not finish initialization before the
launcher was terminated; 280 auth and 280 ping requests were logged.

The MCP stdio contract already limited an individual JSON-RPC line to
2 MiB, but asyncio subprocess stdout used the default StreamReader
buffer of roughly 64 KiB. A legitimate large tools/list response thus
failed long before the configured bound.

- Set the subprocess stream reader limit to the existing 2 MiB bound
  plus one byte.
- Keep explicit 2 MiB input/output payload checks and map a line over
  the limit to MCPTransportError.
- Test a real subprocess returning a >64 KiB single-line response and
  explicit rejection above 2 MiB.
- No new dependency; no change to tools, permissions, REA itself, VMs,
  Docker or Podman.
