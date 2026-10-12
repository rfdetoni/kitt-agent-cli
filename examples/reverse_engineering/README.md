# KITT reverse-engineering MCP: initial read-only slice

This is an experimental stdio MCP server for offline PE/ELF metadata inspection.
It does **not** decompile, debug, execute programs, reverse engineer Windows internals,
or implement the full Windows/Linux differential testing lab.

Run from the repository root:

```sh
python3 -m unittest discover -s examples/reverse_engineering -p 'test_*.py' -v
python3 examples/reverse_engineering/mcp_server.py --workspace /absolute/path/to/test-binaries
```

Configure a user-scoped KITT MCP stdio server pointing to the same Python command
and an explicit workspace root through KITT's existing MCP management. Enable only
in workspaces where the reference binaries may be inspected.

Tool: `binary.inspect` accepts `{"path":"relative/path.exe"}` and returns format,
machine/architecture, section names (for PE), SHA-256 and size. It does not process
imports, symbols or relocation tables yet.

Security: 64 MiB input cap, no process execution, no absolute/parent paths or symlinks.
MCP tooling continues to require normal KITT capability and approval handling.
Do not expose the server over unauthenticated TCP/HTTP.

Next increments: use a mature PE/ELF library for imports and symbol recovery;
sandbox Ghidra headless; add authorized isolated Windows/Linux probe runners,
typed evidence, snapshots and deterministic cross-platform comparisons. Those
capabilities are **not** implemented by this example.
