# KITT RE Lab autosetup: Linux RE and official Dockur Windows

Operator-invoked Docker/KVM lab on Linux. The operator must have a valid
Windows license and authorize the initial Microsoft media download.

## Prerequisites

- Linux Docker Engine accessible to operator.
- /dev/kvm readable and writable, /dev/net/tun available.
- At least 6 GiB available RAM and 40 GiB free disk at initial provisioning.
- Python 3.14+ installed Agent CLI with bundled Oracle assets.
- Network to official Docker registry and KITT Linux RE image source.

## CLI commands

Read-only checks:

    kitt re lab doctor
    kitt re lab status
    kitt re lab setup --runtime docker --windows 11 --linux-profile full --dry-run

Explicit first installation:

    kitt re lab setup --runtime docker --windows 11 --linux-profile full --yes --accept-license

Idempotent lifecycle:

    kitt re lab up
    kitt re lab down

Read-only authenticated Windows and guest manifest handshake:

    kitt re lab verify

Fixed, benign differential event-probe pilot using Windows and current toolbox Wine:

    kitt re lab verify --full

## Ownership and persistence

KITT uses private data under ~/.local/share/kitt/re-lab (0700/0600).
The Linux toolbox is the existing networkless, restricted KITT RE full image.
The Windows container uses only upstream dockurr/windows, pinned by its official
Docker Hub repository digest and its immutable local content identifier.
Windows installation state lives in its own persistent private /storage mount.
The existing OEM first-boot provisioning in re_oracle_bootstrap.py mounts /oem;
Dockur executes install.bat once during the Windows unattended install.
OEM configures OpenSSH, a nonadmin kittprobe user, pinned host key, SSH
ForceCommand runner and registered, hash-pinned probes.
First-install credentials are random and not printed to stdout.
Repeated setup preserves existing container and disk instead of reinstalling.

## Verified states and limitations

- Setup returns PROVISIONING, not READY. Docker running or noVNC is not an
  authenticated Oracle.
- Oracle readiness requires a successful real pinned-SSH forced-command
  probe.info response whose manifest matches the operator-approved hash.
- The full pilot currently uses Wine in the existing networkless toolbox.
  A dedicated disposable Wine candidate is still pending; lab_ready is false
  despite a matching benign pilot.
- No destructive recreate operation is implemented in this CLI. Never use
  broad --yes, retry loops or model prompts to destroy the Windows disk.
- Existing historical Windows Docker/admin installations are not migrated by
  overwriting Docker variables; an explicit supervised migration is required.
- The central KITT installer must be updated to delegate to this canonical
  Agent CLI path before claiming end-to-end one-command ecosystem setup.
- GitHub CI cannot establish Windows/KVM real E2E without an authorized host.
  PowerShell syntax parsing and mock tests are not substitutes for that test.

See docs/WINDOWS_ORACLE_DOCKUR.md for the existing OEM and pilot contracts.
