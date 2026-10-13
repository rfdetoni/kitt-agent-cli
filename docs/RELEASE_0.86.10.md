# Agent CLI 0.86.10 — REA in prebuilt Linux full toolbox

## Changes

- Linux RE `full` container image includes pinned `rea-agents@6.4.0` and Node.js 24.11.0, alongside already supported Ghidra, Frida, Wine, MinGW, GDB and LLDB.
- Node.js and REA use an isolated multi-stage Docker build. `base` and `wine` image tiers remain free of REA and its Node dependency.
- The existing, optional `kitt-rea` plugin gains `rea_workflow`, a guidance-only tool for native PE/ELF, JavaScript/Electron, .NET/managed, version comparisons, recorded crashes and PCAPs.
- No duplication of REA MCP server or Evidence model; REA executes only via the existing KITT MCP approval and audit boundaries.
- Published REA tool identifiers are verified against the 6.4.0 source contracts. No general VM shell, Docker socket or automatic OS network permissions are added.

## Build and verify

```sh
kitt vm container build --runtime podman --image toolbox --profile full
kitt vm container toolbox --runtime podman --profile full --workspace "$HOME/projects/authorized-analysis"
# Inside image:
rea --version
rea doctor --provider ghidra --json
```

Host `kitt-rea` MCP remains an explicitly enabled plugin and needs a host-installed REA CLI. REA inside the toolbox does not automatically appear on the host.

## Checks

Release-critical CLI tests and Docker build checks cover plugin behavior, packaged image builds and REA executable availability. A live cross-VM Windows/Linux REA mission remains a separate host environment validation.
