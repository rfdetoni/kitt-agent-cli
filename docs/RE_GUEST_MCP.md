# KITT RE guests: MCP bridge (operator-only, read-only)

The module `python -m kitt.re_guest_mcp --role operator` provides a minimal JSON-RPC 2.0 / MCP stdio server. It is a **host-side adapter**; do not install a network-listening MCP daemon inside Windows or Linux guests.

## Available tools

- `guests_status`: inspected Docker status for operator-managed Linux and Windows RE guests, with paths, container IDs, credentials, console URLs and raw guest data removed.
- `windows_oracle_doctor`: uses the existing pinned, forced-command SSH readiness protocol. Requires an independently known lowercase SHA-256 of the *existing* Windows probe manifest. It does not run the probe or return results from Windows internals.

No shell, `docker exec`, file mutation, code upload, browser control, arbitrary tool arguments, or cross-guest transfer is exposed. Responses are bounded; malformed/oversized JSON and unknown operations are rejected.

## Configuration

For an operator-controlled KITT workspace, add a restricted MCP server entry to the trusted `.kitt/mcp.json` config:

```json
{
  "mcp": {
    "re-guests": {
      "transport": "stdio",
      "command": "python3",
      "args": ["-m", "kitt.re_guest_mcp", "--role", "operator"],
      "trust": "restricted",
      "allow_tools": ["guests_status", "windows_oracle_doctor"],
      "timeout_seconds": 30,
      "max_output_bytes": 16384
    }
  }
}
```

The KITT MCP Manager's configuration and host security policies remain the authority. The operator must approve installing/enabling this entry and ensure the module is installed in the selected Python interpreter. No server is automatically activated by this release.

## Clean-Room boundary

**Never mount or enable this MCP server in an isolated Wine Implementer agent.** The Analyst may use operator-managed Oracle readiness checks; the Implementer receives only an independently reviewed public API contract. A planned future release can add separately authorized **hash-pinned probe execution** as a distinct capability, but it must not expose arbitrary SSH commands or guest filesystem access.

Run `python -m unittest tests.test_re_guest_mcp` (or the targeted test suite). The tests prove server framing, denial of arbitrary execution, denial of unknown tools, manifest validation and metadata redaction, not guest attestations. Full Windows/Wine differential validation remains separate.
