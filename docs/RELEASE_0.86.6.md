# Agent CLI 0.86.6 — reverse-engineering guests and VM supervisor

## Scope

- Adds an optional read-only reverse-engineering MCP evidence query; preserves the existing tool PolicyEngine and approval path.
- Adds operator-only Ghidra Headless function exporter and fixed Frida Win32 API event instrumentation, for isolated guest VMs.
- Adds a local SQLite archive for validated probe observations, along with a hash-pinned Wine probe runner.
- Adds a dual-guest forced-command SSH differential mission controller. The MCP tool only plans mission argv; governed host execution is still mandatory.
- Adds operator-only `kitt vm doctor` / `kitt vm run`, which launch a disposable QEMU guest from a SHA-256-pinned standalone qcow2 image with QEMU `restrict=on`, explicit network forwards and SSH host key pinning.
- Guest `vm_worker` runs durable KITT contracts under `--contract --contract-yes --allow all`, without spawning the resident daemon.
- Adds dedicated boundary regression tests and documentation. The bundled plugin remains disabled by default.

## Compatibility

No wire protocol changes, new Python third-party dependencies, or modifications to model gateway contracts.
The agent requires Python 3.14+ as before. QEMU/OpenSSH are OPTIONAL Linux-host prerequisites for `kitt vm`.
Agent version advances from 0.86.5 to 0.86.6. The optional plugin version advances to 0.2.0.

## Limits and production gates

CI exercises simulated VM boundaries but not an actual boot of QEMU, Windows 11, Frida, Ghidra, or Wine.
No user-owned host data are mounted into the guest, and VM output is advisory until the host independently verifies it.
VM image creation, guest setup, source materialization, guest trust attestation, source patch review, remote model gateway policy and production hardening are operator responsibilities.
Do not expose general shell execution over MCP, do not provide broad host credentials to guests, and do not conflate guest autonomy with host authority.

See `docs/VM_SUPERVISOR.md` and `docs/VM_AUTONOMY_SECURITY.md`.
