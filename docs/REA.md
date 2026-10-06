# REA integration

KITT Agent CLI can expose an installed [REA](https://github.com/morluto/rea) instance through its existing governed Model Context Protocol boundary. The integration is optional and disabled by default.

## Enable

Install REA separately using its upstream instructions, then enable the bundled KITT plugin:

```bash
kitt plugins enable kitt-rea
```

On the next Agent startup the plugin resolves the local `rea` executable and registers exactly:

```text
rea mcp
```

as the local stdio MCP server `rea`. REA tools are exposed through:

```text
mcp.rea.*
```

Use `rea_inspect` to see whether the CLI is installed and whether the MCP adapter is registered.

## Trust boundary

KITT does not invoke `npx`, download REA, install Ghidra/Hopper, alter REA providers, or bypass REA authorization. If `rea` is absent, the plugin remains inactive. If a user already configured an MCP server named `rea`, the plugin leaves that registration untouched.

Every REA MCP tool still enters KITT's existing ToolRegistry, policy, approval, audit, cancellation and output-bounding paths. The plugin owns only registrations it created and removes only those registrations on shutdown.

## Evidence semantics

REA observations stay in KITT's existing evidence plane. Evidence v2 distinguishes observation, inference, claim and unknown while retaining authority, confidence, coverage, limitations, producer metadata and a provenance digest. A REA MCP tool observation is tagged with `authority=rea.mcp`; that identifies the producer and does not automatically prove unrelated runtime behavior.

The task-plan coordinator separately projects verification obligations and residual unknowns from host-owned facts. This is a view over existing state, not a second scheduler or ledger.
