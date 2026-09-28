# Figma integration

KITT Agent CLI integrates with Figma through the official Model Context Protocol (MCP) boundary rather than embedding Figma credentials or a second HTTP client in the Agent.

## Enable the plugin

```bash
kitt plugins enable kitt-figma
```

On the next Agent/daemon startup, the plugin registers the official Figma desktop MCP endpoint as `figma-desktop`:

```text
http://127.0.0.1:3845/mcp
```

Enable the desktop MCP server inside the Figma desktop application (Dev Mode → MCP server). When it is reachable, KITT exposes its tools under:

```text
mcp.figma-desktop.*
```

The plugin itself never contacts Figma directly and never reads a Figma token.

## Inspect a Figma target

The bundled read-only helper `figma_inspect` accepts a Figma URL and normalizes its file key and node id. It can also perform a bounded workspace scan for Figma links:

```json
{
  "url": "https://www.figma.com/design/AbCd1234/My-App?node-id=12-34",
  "scan_workspace": true
}
```

The result reports the normalized target, configured Figma MCP servers, the preferred server, and the MCP tool prefix to use for subsequent design-context operations.

## Hosted Figma MCP

Figma also publishes the hosted endpoint:

```text
https://mcp.figma.com/mcp
```

Figma currently restricts hosted MCP access to supported/catalog clients and the remote flow requires authorization. KITT therefore does not silently bootstrap OAuth or store credentials for that endpoint. If a hosted Figma MCP connection is separately authorized and configured in KITT, `figma_inspect` prefers it over the desktop adapter.

This distinction matters because Figma's hosted MCP currently exposes the broadest feature set, including newer write-to-canvas workflows, while the desktop MCP is the compatibility path KITT can safely register without impersonating a catalog client.

## Lifecycle and security

`kitt-figma` is disabled by default. Its `mcp.manage` permission is scoped through `PluginContext`; plugin-owned runtime MCP registrations are removed when the plugin unloads. Existing user-configured MCP entries are never overwritten or deleted by the plugin.

All Figma MCP tools still enter the normal KITT ToolRegistry, policy, approval, audit, and cancellation paths.
