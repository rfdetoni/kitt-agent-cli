# Agent CLI 0.86.9 — authenticated Docker/Podman VM lifecycle

## P1 fixes

- Per-VM generated Basic-auth secrets and nginx reverse proxy for all noVNC/WebSocket connections (including peers sharing a container bridge). Internal websockify listens only on loopback.
- QEMU runner starts via immutable local image sha256 ID stored in the profile; mutable tags cannot silently change a configured guest.
- QEMU startup waits for actual container status and authenticated console HTTP 200. Concurrent attempts are serialized by a host-owned `flock`.
- Persistent container records and bounded, owner-only logs retained before restart. Stopped containers can be inspected without loss.

## Operational additions

- Offline baseline snapshots (`kitt vm container snapshot --action save/restore/list`), including disk, UEFI and TPM state; restore requires `--yes`.
- Optional `--probe-port` enables *host-to-guest-only* SSH by a QEMU `restrict=on` forwarding; disabled by default, with no guest NIC.
- Host-controlled multi-guest probe broker `kitt vm lab run/doctor` reuses SSH forced-command and hash-pinned receipt comparison. Requires operator-provisioned guest SSH endpoints, not a guest shell.
- `kitt vm create` asks the backend in interactive mode; explicitly rejects container `--tools`/`--distro` flags that were not used.
- `kitt vm container credentials --profile ...` prints the viewer password on explicit request only.

## Migration and validation

VM container profiles have a new required image ID and viewer credential files. Stop and back up VMs created with 0.86.8, then recreate a profile in the new wizard. Do not hand-edit profiles to spoof immutable identities.

CI tests the safety contracts, entrypoint, cross-platform package and container builds. Live Windows, Linux and Podman rootless guest boot, SSH provisioning and malware-proof containment require dedicated host testing and are not proven solely by CI.
