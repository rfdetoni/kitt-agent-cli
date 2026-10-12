# Unattended KITT inside disposable VMs — architecture and security contract

## Goal

Minimize approvals while allowing a coding/reverse-engineering agent to run shell,
build systems, debuggers, installation tooling and long workflows. The authority
boundary must be outside the agent-controlled VM. A VM alone is **not** an absolute
security guarantee against network pivoting, exfiltration or hypervisor escapes.

## Recommended deployment

```text
Trusted host: operator + VM supervisor + authenticated model gateway + artifact reviewer
   |-- disposable Linux agent VM: KITT with VM-local autonomous execution
   |-- disposable Windows oracle VM: test-only Windows SDK/Frida/WinDbg
   `-- disposable Linux/Wine VM: test-only Wine/Ghidra/GDB
```

- Use KVM/QEMU/libvirt or equivalent hypervisor and immutable, hash-pinned guest images.
- Allocate a clean writable overlay per mission. No writable host mounts, clipboard, USB/PCI/GPU passthrough, Docker socket, SSH agent forwarding or host credential mounts.
- Default deny guest egress. Permit only a dedicated authenticated model gateway and isolated test endpoints through a host-controlled firewall. Package installation needs a controlled cache/mirror; `allow-all` must not implicitly grant Internet access.
- Assign mission-local, expiring model-gateway credentials with bounded model spend, rate limits and no rights to host files or GitHub pushes. Never give a guest personal tokens or access to the user's LAN.
- The *host* supervisor owns VM creation, guest isolation validation, kill, snapshot restoration, audit, artifact intake and promotion to repositories. Guest code cannot change these controls.
- The host provides a disposable source copy, not a bind-mounted writable trusted checkout. Output flows back through a limited artifact channel with size/content checks and deterministic builds/tests.
- Treat binary analysis, external documents, and model outputs as untrusted, even inside VM. Prevent guest logs and traces containing secrets from automatically entering KITT Memory.

## Two-level autonomy

**Guest-level freedom**: inside a proven isolated guest, the KITT runtime may
allow shell commands, build tools, package operations and subagent edits without
one approval per command. The VM-local sandbox profile may be `full-access`
because the *outer VM* owns the actual host security boundary.

**Host-owned irreversible operations**: network egress to unspecified endpoints,
token use, modifications outside the disposable workspace, VM configuration,
host data access, hardware passthrough, Git pushes, publishing, and deployment
must remain unavailable to the guest or require authorization from the host.

Never infer that KITT is in a VM from an environment variable, hostname,
device name, WSL detection, or guest-side flags. Only the outside supervisor can
grant mission-scoped autonomy and validate hypervisor/network/storage isolation.
Do not silently change the current `/autonomy allow-all` behavior on host.

## Mission lifecycle (target design, not implemented by this PR)

1. Operator authorizes one bounded **mission policy** and selects an approved image, workspace, cost budget and network allowlist.
2. Host supervisor verifies image digest, creates disposable VM overlay, attaches an isolated network, provisions temporary credentials and audits startup.
3. KITT starts inside guest with VM-local command freedom; plan/execute/validate/retry proceed until verified, budget exhausted, or no progress.
4. KITT talks to Windows and Wine oracle VMs over fixed authenticated channels. The current `mission.py` runner coordinates only operator-pinned probes with a forced SSH command; it is **not** an arbitrary guest shell provider.
5. All tool calls, derived code and guest outputs are recorded as bounded evidence. Hashes detect accidental corruption, not VM authenticity.
6. Host receives changed files and verification receipts over a size-limited channel, scans diffs and reruns critical checks **outside** the untrusted guest.
7. The host, not guest, decides commit/push/merge. Tear down all guests, revoke temporary credentials and remove overlays at mission end or failure.

## Acceptance gates for a real VM supervisor

- Prove no guest can read host private keys, personal documents, cloud metadata or private network services.
- Prove egress denial at the hypervisor/firewall boundary, including IPv6 and DNS rebinding.
- Prove VM overlay/snapshot restoration before and after missions, including on guest crash or host cancellation.
- Prove resource quotas, process cancellation, bounded artifacts, no orphan VMs and no leaked temporary tokens.
- Prove identity binding of the VM and mission to the supervisor; a guest-generated SHA-256 receipt is insufficient.
- Prove host-side diff review and independently executed tests before any push/merge/release.
- Run genuine isolated Windows, Wine, Ghidra and Frida E2E. Simulated unit tests are insufficient.

## Current implementation

The PR #82 adds only the bounded tools and `lab_mission_plan` (read-only MCP)
plus operator-invoked guest RPC for allowlisted probes. It does **not**
automatically deploy the described VM supervisor or grant VM-local full shell
autonomy. That requires a separate supervisor with enforced hypervisor-level
security, lifecycle management, and real end-to-end validation.
