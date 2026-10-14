# Lazy RE environments

The RE manager is **operator-owned**. Ordinary `kitt` startup does not
start a Podman/Docker container, create a VM, pull images, or download a guest OS.

## First-run checks and TUI

- Run `kitt re status` or open the command palette and choose **Ambientes RE /
  Laboratório** (`/re`).
- Select Windows or Linux; **[p]** opens a guided TUI ISO/QCOW2 path field.
  Press **Tab** to select Podman or Docker. You must supply your own
  licensed media. The wizard creates a persistent private profile and prints
  the installation command.
- If the pinned QEMU image is missing, choose **[b] Build imagem** first.
  Building can download tool packages; it is never performed during normal
  KITT startup. A missing Podman/Docker installation requires operator
  installation, not automatic privilege escalation.
- Complete the guest OS installation using noVNC. Shutdown guest, restart
  without `--install`, install and pin the authorized forced-command
  `guest_rpc` probe runner before attempting live differential missions.
- Select **[e]** to enroll the installed VM's existing profile, then **[a]**
  to opt into mission-triggered startup. The TUI assumes the default
  per-user profile path; for custom paths use the CLI.

```bash
kitt re setup windows --runtime podman --source ~/ISOs/Windows11.iso
kitt vm container start --profile ~/.local/share/kitt/vm-containers/windows-podman/profile.json --install
# After OS installation and shutdown:
kitt re enroll windows ~/.local/share/kitt/vm-containers/windows-podman/profile.json
kitt re auto windows on
kitt re status
```

The analogous Linux profile uses `linux-podman`. Enrollment verifies private
profile structure, OS identity and pins SHA-256. An edited profile fails
closed until the operator enrolls it again. **Enrollment is an operator
attestation that installation and guest isolation are complete**, not an
automated guest health/probe trust check.

## Automatic lifecycle boundaries

An operator-invoked `kitt vm lab run --hosts ... --mission ... --workspace ...`
starts only enrolled Windows/Linux guests with `auto=on` when needed. A
running guest is reused, not started twice. The container supervisor still
handles its own lifecycle locking, image digest and noVNC health checks.
Turn-level code tasks and static REA analysis do not boot VMs.

`kitt re start windows` and `kitt re stop windows` are explicit operator
actions; `/re` has the same controls. This release **does not** implement
automatic VM shutdown, time-based eviction, reference counting across
independent KITT processes, or a universal guest OS provisioning service.
Do not stop a guest while a probe is in flight. No host-level Docker/Podman
socket, guest admin credentials, unrestricted shell, or network access is
granted to the model. Live Windows/KVM integration still requires the
operator's authorized lab.

## REA and toolbox

The `full` image has Ghidra, Frida, Wine and REA. `[b]` on Toolbox builds
this pinned image only on explicit user request; `kitt vm container toolbox`
starts an ephemeral, no-network shell for an authorized workspace. It is
not automatically treated as a sandbox for hostile binaries. The REA MCP
plugin remains explicitly opt-in and requires a host-side `rea` executable
if used outside the toolbox:

```bash
kitt re setup rea
kitt plugins enable kitt-rea
kitt re setup toolbox
kitt vm container toolbox --runtime podman --profile full --workspace "$PWD"
```

Use `kitt vm container doctor --runtime podman` and
`kitt vm lab doctor --hosts /path/to/hosts.json` to diagnose hardware
and forced-command guest probe readiness. Real multi-guest acceptance
must be performed on the intended host before treating evidence as verified.
