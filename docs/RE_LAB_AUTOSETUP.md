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

## Explicit reset

Preview and obtain the confirmation token without mutation:

    kitt re lab recreate --scope windows --destroy-data --dry-run

Execute ONLY with that recent token:

    kitt re lab recreate --scope windows --destroy-data \
      --confirm TOKEN_FROM_DRY_RUN --accept-license

Other scopes: linux, wine, all. The token is bound to scope, selected
immutable Docker container IDs, absolute deletion paths and a ten-minute
expiry; all scopes require --destroy-data. A failed reinstall reports PARTIAL
rather than declaring readiness. The global workspace is never deleted by
the windows-only scope.

## Oracle diagnosis (safe, non-destructive)

Use `kitt re lab verify --full` only after Windows provisioning completes. It checks
pinned SSH and manifest **before** running any differential pilot.
`SSH_UNAVAILABLE` means unreachable/refused transport; `SSH_TIMEOUT` means
connection timeout; `SSH_DISCONNECTED` means a closed connection;
`SSH_AUTH_FAILED` means public-key login denied; `SSH_HOST_KEY_REJECTED`
means the pinned identity was rejected. `SSH_FAILED` covers unrecognized SSH
errors. `BLOCKED_ENV` remains for other environment or contract failures.

During first boot, inspect `http://127.0.0.1:8006` and the guest's
`C:\\KittOracle\\bootstrap.status` (PROVISIONING, FAILED or READY).
Neither a running Docker container nor PROVISIONING means Oracle READY.
Retry is limited to one additional **read-only** probe.info handshake for
recognized transient transport errors; no automatic setup, reset, key
replacement or probe.run replay is allowed. SSH stderr is never copied into
the CLI response. Operator diagnostics:

    docker port kitt-re-win11 22/tcp
    docker logs --tail 100 kitt-re-win11
    kitt re lab verify

Never run `recreate` just to recover a refused SSH connection.

## Verified states and limitations

- Setup returns PROVISIONING, not READY. Docker running or noVNC is not an
  authenticated Oracle.
- Oracle readiness requires a successful real pinned-SSH forced-command
  probe.info response whose manifest matches the operator-approved hash.
- The fixed pilot uses a separate ephemeral networkless, read-only
  Linux+Wine container with strict limits and only a read-only approved probe
  mount. It shares the Linux kernel; it is not a Windows guest or hypervisor.
- A scoped destructive recreate requires a read-only plan and recent HMAC
  confirmation token bound to Docker IDs and absolute paths, plus explicit
  --destroy-data and --accept-license for Windows resets. Never use broad --yes
  or a model-generated token to destroy the Windows disk.
- Existing historical Windows Docker/admin installations are not migrated by
  overwriting Docker variables; an explicit supervised migration is required.
- The central KITT installer must be updated to delegate to this canonical
  Agent CLI path before claiming end-to-end one-command ecosystem setup.
- GitHub CI cannot establish Windows/KVM real E2E without an authorized host.
  PowerShell syntax parsing and mock tests are not substitutes for that test.

See docs/WINDOWS_ORACLE_DOCKUR.md for the existing OEM and pilot contracts.
