# KITT Agent CLI 0.86.10 — REA 6.4.0 in full Linux toolbox

## Shipped

- Preinstalled published `rea-agents@6.4.0` and compatible Node.js 24.11.0 in the **full** Debian RE Docker/Podman toolbox, alongside existing pinned Ghidra, Frida, Wine and GDB/LLDB.
- Multi-stage container build avoids adding Node.js to `base`, `wine` or ordinary KITT coding-agent installations; `GHIDRA_INSTALL_DIR` is set in full.
- Full image CI smoke verifies Node, REA, Ghidra and Frida, and publishes versioned GHCR tags only after merge.
- Optional `kitt-rea` plugin v1.1.0 gains `rea_workflow`, a deterministic read-only selector for native, JavaScript, Electron, .NET, comparisons, recorded crashes and packet captures.
- `rea_workflow` returns guidance only: actual REA MCP calls still obey KITT approval, lifecycle and existing canonical Evidence boundaries.
- Docs/REA.md, Docker/Podman manual, README and focused regression tests updated.

## Deliberate limits

- No REA or Node installed into Windows/Linux guest ISOs, Docker host, or native Debian agent VM automatically.
- An executable installed in a toolbox container is not automatically exposed to a separate host `kitt-rea` MCP plugin. Explicitly install REA on the host if host-side MCP access is required.
- No duplicate Evidence store, orchestration service, unconstrained VM shell or new privilege grants.
- Provider readiness and live Windows/Linux Podman boot remain environment-specific acceptance gates; CLI/build smokes are not substitutes.
