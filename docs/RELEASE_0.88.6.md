# Agent CLI 0.88.6 — disposable Windows Oracle bootstrap

- Add `kitt re oracle bootstrap --recreate --yes --accept-license` for an existing disposable Dockur lab, or omit `--recreate` on a new lab.
- Generate Ed25519 client/host keys, pre-pinned known_hosts, private OpenSSH host profile, fixed MinGW-compiled Win32 event probe and immutable manifest digest.
- First-boot OEM scripts install Windows OpenSSH.Server, a nonadmin probe account, public-key-only authentication and a fixed PowerShell ForceCommand probe.info/probe.run endpoint. Only locally registered SHA-256-pinned binaries can execute.
- Bind Windows SSH solely to host loopback 127.0.0.1:2222; preserve noVNC 8006 loopback. User-mode Dockur forwarding uses USER_PORTS=22.
- Add read-only `kitt re oracle doctor` and `kitt re status --check-oracle`; never assume Windows readiness from container presence.
- Destructive replacement requires exact verified KITT Docker identity, private lab root, explicit user consent and image/compiler preflight. No Linux workspace or other disks are deleted.
- Bump pyproject, Python uv lock, version metadata and documentation; keep existing QEMU guest profiles and approval boundaries.

- Add operator-only fixed event_auto_reset Windows × Docker Wine pilot and SHA-bound evidence at oracle/pilot.json, with blocked/partial results kept distinct from successful matching.

**Validation:** CI unit/contract checks and packaging; actual Dockur Windows 11 first-boot OpenSSH + pilot probe E2E remains an operator-environment validation gate. Failures remain BLOCKED_ENV and must not be reported as an authenticated oracle.
