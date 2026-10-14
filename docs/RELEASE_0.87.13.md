# Agent CLI 0.87.13 — lazy RE lab controls

- Added operator-owned RE inventory, explicitly pinned/enrolled Windows/Linux
  VM profiles, per-guest opt-in lazy startup and idempotent reuse at the
  existing lab command boundary.
- Added `kitt re status|setup|enroll|auto|start|stop`, with errors that
  distinguish missing environments from blocked or invalid profiles.
- Added `/re` to the command palette, RE manager overlay with mouse/keyboard
  controls and an ISO/QCOW2 helper using the existing profile wizard.
- Added explicit pinned QEMU/toolbox image builds in the TUI; **no**
  automatic host package install, ISO download, sudo, privileged container,
  host Docker socket mount, or model-owned hypervisor access.
- Version bumped from 0.87.12 to 0.87.13 in metadata, fallback and uv.lock.

Unit regressions cover no eager boot, opt-in, idempotency and tamper detection.
Hardware acceptance on actual Podman/KVM/Windows guests remains required.
See [RE_LAZY_ENVIRONMENTS.md](RE_LAZY_ENVIRONMENTS.md).
