# REA integration

KITT Agent CLI can expose an installed [REA](https://github.com/morluto/rea) instance through its existing governed Model Context Protocol boundary. The integration is optional and disabled by default.

## Enable

The KITT root installer `--preset full` includes REA 6.4.0 in a private prefix by default; use `--skip-rea` to omit it, or `--with-rea` with other presets. You may instead install upstream REA manually. Only enabling `kitt-rea` registers it as an MCP server.


After installing REA via KITT root installer or upstream instructions, enable the bundled KITT plugin:

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

## REA included in the Linux RE `full` toolbox (Agent 0.86.10+)

The Docker/Podman `full` toolbox now installs the published **REA 6.4.0** CLI
with a supported **Node.js 24.11.0** runtime, in addition to pinned Ghidra,
Frida, Wine, MinGW and the base debugging toolchain. Other profiles do not
install Node or REA. Debian Trixie's distro Node.js 20 does not meet REA's
Node engine requirement, so the image copies only the required runtime and
REA installation from a separate build stage.

```sh
kitt vm container pull --runtime podman --image toolbox --profile full
# Or build locally:
kitt vm container build --runtime podman --image toolbox --profile full
kitt vm container toolbox --runtime podman --profile full \
  --workspace "$HOME/projects/authorized-analysis"
# Inside the toolbox:
node --version
rea --version
rea doctor --provider ghidra --json
rea analyze-javascript-application /workspace/example-app --json
rea inspect-managed-artifact /workspace/example.dll --json
```

`GHIDRA_INSTALL_DIR` is set to `/opt/ghidra` in that image. Provider health
still depends on each artifact and runtime constraints. The CLI runs **inside
the container**: installing it there does not automatically register an REA
MCP server in a KITT process **outside** the container. For host MCP, install
a compatible REA CLI on that host separately and enable `kitt-rea`.

Toolbox workspaces are mounted from an operator-specified path. Treat REA's
output as analysis evidence and preserve target identity and SHA-256. The
toolbox is not a VM-strength boundary; execute suspicious binaries in a
disposable guest, not in the toolbox. The KITT VM probe broker uses separate
forced-command SSH guests and does not authorize unrestricted VM shells.

## Guided workflows without a duplicate orchestration engine

`rea_workflow` is a lightweight KITT tool that accepts `target_type`:
`native`, `javascript`, `electron`, `managed`, `comparison`, `crash` or
`network`. It returns suggested **existing REA tool names**, a bounded
preflight command and limitations, but **does not execute** tools,
provision a VM or attach to a process. Typical investigations:

| Target | Suggested REA analysis | Evidence boundary |
| --- | --- | --- |
| Native PE/ELF | `open_binary`, `analyze_function`, `get_call_graph` | Static only until runtime observed |
| JS application / Electron | `analyze_javascript_application`, `inspect_electron_page`, `reconcile_javascript_runtime` | Static graph ≠ observed process |
| .NET / NativeAOT | `inspect_managed_artifact`, `inspect_managed_members`, `verify_managed_native_boundaries` | Native calls need separate evidence |
| Different versions | `compare_application_versions`, `compare_managed_members`, `compare_functions` | Match comparable, authenticated input Evidence |
| Crash and network | `inspect_recorded_crash`, `inspect_packet_capture` | Recorded artifacts, not live attach or capture |

Use REA's advertised MCP schemas for the current installed version instead
of relying on a hardcoded wrapper with arbitrary extra flags. The tool result
remains REA's canonical Evidence. KITT retains its single host-owned approval
and evidence/verification ledgers and explicitly separates observations,
inferences and unknowns. Do not mark behavioral parity established by
decompiled source alone.

## Investigation sequence with Windows/Linux VM lab

1. Perform authorized static artifact inspection in the `full` toolbox.
2. For behavior, prepare Windows reference and Linux/Wine candidate VMs
   separately and install the pinned forced-command SSH probe runner.
3. Run `kitt vm lab run --workspace ... --hosts ... --mission ...` using
   operator-approved probes and retain receipts with artifact/probe SHA-256.
4. Correlate returned evidence (do not invent equivalence) and revise the
   reconstruction until the host verifier closes the relevant obligations.

The lab mission controller does not automatically copy tool output from the
toolbox into VM guests; transferring artifacts is a separate approved step.
REA does not receive Docker or Podman sockets, guest credentials or
automatically broad network access from this integration.
