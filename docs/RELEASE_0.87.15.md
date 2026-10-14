# Agent CLI 0.87.15 — managed lazy RE lifecycle

Builds on 0.87.14 without changing the KAP/2 wire contract or host
authorization boundary.

- **One host-owned lease per lab mission**: `kitt vm lab run` starts opted-in
  guests only on demand, serializes concurrent lazy missions, and stops only
  guests started by this mission after success, validation failure or exception.
  Pre-existing running guests remain running.
- **Toolbox in TUI**: the `/re` manager now opens the pinned RE toolbox shell
  in a temporarily suspended terminal and restores the TUI on exit. The
  selected workspace is mounted inside an ephemeral, network-disabled
  container by the existing `vm_container.toolbox` implementation.
- Regression cases cover auto-stop, exception cleanup, protecting pre-existing
  guests and single active lab lease.
- Bumped metadata, source fallback and `uv.lock` to **0.87.15**.

Limits: no automatic guest OS installation, no provider credentials forwarded
to guests, and no fully crash-proof VM recovery. Real Podman/KVM/Windows
guest integration still requires hardware acceptance. See
[RE_LAZY_ENVIRONMENTS.md](RE_LAZY_ENVIRONMENTS.md).
