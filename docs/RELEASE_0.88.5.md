# Agent CLI 0.88.5 — Docker/Podman auto-selection and managed RE lab reuse

## Changes

- CLI `kitt vm container build|pull|doctor|toolbox` and `kitt re setup` default to runtime `auto`: prefer a responding Docker Engine and fall back to Podman only when Docker is unavailable. An explicit engine remains authoritative.
- `kitt re status` identifies existing installer-managed `kitt-re-full` and `kitt-re-win11` Docker containers after validating their KITT ownership labels, immutable image identity, expected private bind mounts, loopback Windows console and Linux toolbox isolation. It remains read-only and reports invalid containers as blocked.
- `kitt re setup linux|windows` without `--source` discovers the existing Docker lab rather than demanding another ISO; explicit `--source` continues to select the separate QEMU guest flow.
- Operator `kitt re start|stop linux|windows` acts on the verified Docker container ID; `kitt re shell linux` provides human-TTY shell access to the managed Linux toolbox. No new containers, OS image downloads or disk replacements are performed in these commands.
- Docker discovery never enrolls a QEMU profile, starts a mission, supplies arbitrary host execution to the agent or grants an authenticated Windows oracle. `oracle_ready=false` until the independently provisioned SSH forced-command channel has been validated.

## Validation

- Focused identity, boundary and lifecycle regressions; the full Agent CLI regression suite.
- Docker/Podman smoke tests; Windows package smoke; static/release-critical checks.
- The installer-managed Docker lab is distinct from `kitt vm container` QEMU guests. Live-host Dockur OS readiness and guest probe authorization remain outside this release.
