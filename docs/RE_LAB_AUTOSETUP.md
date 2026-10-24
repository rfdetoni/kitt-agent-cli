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

## Installer-managed images

On Linux, the standard KITT installer now repairs all three RE Docker images
automatically **when it detects an existing lab** under
`~/.local/share/kitt/re-lab`. New operators can explicitly include them:

```bash
./install.sh --with-re-tools
# or on an already-installed Agent CLI:
kitt re lab images status
kitt re lab images prepare --runtime docker
```

The prepare action ensures the version-pinned full RE Toolbox, Ghidra and
Frida images, reusing existing images or trying GHCR pull first with an
official Dockerfile build fallback. All images must pass offline smoke
checks or the installation reports failure. The installer does **not**
create or reset the Windows Oracle and does **not** make Docker accessible
from a confined model-execution sandbox. `--no-re-tools` explicitly
skips image provisioning during installation.

KITT root agents can call `kitt_runtime` `re.lab.images` for read-only host
image readiness or `re.lab.analyze` with `{"path":"2.1.308"}` and optional
`resume_job`/`dry_run` for the fixed Ghidra host workflow. Execution
requires the `process.run` capability and passes the host `re_lab_analyze`
policy and approval checks. Path-scoped child agents are denied. This operation
is synchronous and may occupy one KITT tool turn for a long analysis; project
and logs remain persistent. No generic Docker argument or host mount is accepted.

When a model's `process.run` executes in a strong sandbox, Docker's
`/var/run/docker.sock` can legitimately be absent even though host Docker
is healthy. Neither building images nor chmod/chown of the socket resolves
that isolation boundary: run approved RE jobs using host-owned KITT RE
operations. Never mount the Docker socket or use `--privileged` merely
to bypass sandbox isolation.

## Dedicated analyzers (0.88.77, no old-toolbox fallback)

The existing Linux toolbox keeps REA/Wine; it no longer bundles Ghidra or
Frida. First install the dedicated, versioned images (once) using the operator
CLI, independently of the Windows guest:

```sh
kitt vm container pull --image ghidra --runtime docker
kitt vm container pull --image frida --runtime docker
# If GHCR is unavailable, build directly from verified project Dockerfile:
kitt vm container build --image ghidra --runtime docker
kitt vm container build --image frida --runtime docker
```

`kitt re lab analyze --path 2.1.308` now requires the Ghidra-only image;
it does not use or fall back to the old all-in-one `kitt-re-full` image. The
old saved `KittAnalysis.gpr/.rep` projects remain valid since the Ghidra
version remains 12.1.4, and the operator can resume them in place.
The dedicated static analysis image has no Wine/REA/Frida dependencies;
Ghidra heap and CPU limits remain per-job, and the process is networkless,
with immutable image ID and workspace mounted read-only.

The Frida image has its own operator-only, offline Linux instrumentation
shell. The default container lacks ptrace privileges; the opt-in grants
SYS_PTRACE only within an isolated process namespace:

```sh
kitt vm container toolbox --profile frida --workspace "$PWD" --runtime docker --allow-ptrace
# Inside guest: frida --version
```

There is no --privileged or --pid=host, no general socket/network exposure,
and it cannot attach to Windows guest processes from the Linux container.
For Windows-specific Win32 tracing the authorized Frida agent still runs
inside the Windows guest. Image separation improves deployment and memory
isolation, not the inherent runtime of a particular Ghidra analyzer.

## Active project workspace shared with RE analysis (no copying)

The KITT RE tooling now supports zero-copy, on-demand, isolated Linux analysis
against the **currently selected project** (current working directory by default).
No special host staging or manual `cp` is needed:

```bash
cd ~/projects/claude
kitt re lab analyze --path 2.1.308 --dry-run
kitt re lab analyze --path 2.1.308
```

The operator CLI resolves the independently installed `kitt/re-ghidra`
image to its immutable local Docker SHA256 ID and starts a **short-lived
Ghidra-only container**, without modifying the persistent Linux toolbox or
Windows guest.
The active project is mounted at `/workspace` **read-only**, and a new
private run directory under `./reverse-engineering/decompiled/ghidra/` is
mounted at `/output` read/write. The original executable is independently
SHA256-checked before and after; Ghidra's project and logs appear in the
current project without transferring copies. Output files and logs are visible
to the operator without further synchronization.

Isolation: network disabled, no privileged Docker/host socket, caps dropped,
read-only container root, bounded CPU/RAM/PIDs and a controlled Java heap.
**The full project source tree, including any sensitive files in the project, is
readable inside this operator-invoked container.** Only use trusted workspaces;
move credentials outside the project. `--dry-run` performs validation without
creating folders or running a guest. No automatic MCP authorization is added.
The Windows Oracle remains unchanged.

Ghidra and host total execution limits are **disabled by default**, so slower
machines can keep processing for hours. A 15-second stderr heartbeat reports
elapsed duration, bounded incremental log growth and, if available, Docker
CPU percentage, resident memory versus container limit, and block I/O changes.
These are **activity indicators, not progress percentages or ETAs**. Lack of
log activity alone does not stop the job; a 600-second inactivity warning
never kills it. Missing Docker stats mean unavailable telemetry, not a hang.
Stdout contains only one structured JSON result at the end.

Optional operator controls (memory and CPU limits do not increase by default):

```bash
kitt re lab analyze --path 2.1.308 --resume-job job-27ee75656097
kitt re lab analyze --path 2.1.308 --resume-job job-27ee75656097 --stall-warning-seconds 1200
kitt re lab analyze --path 2.1.308 --resume-job job-27ee75656097 --host-timeout-seconds 14400 --analysis-timeout-seconds 13800
kitt re lab analyze --path 2.1.308 --resume-job job-27ee75656097 --memory-mib 3072 --heap-mib 1536 --cpus 1
```

The default 4096 MiB container memory and 2048 MiB headless Java heap
follow the Ghidra launcher `GHIDRA_HEADLESS_MAXMEM` convention. At least
512 MiB must remain outside the Java heap. The supervisor uses one bounded
Docker stats query per 15-second heartbeat, not a sidecar or a full log rescan.
Each job uses an exclusive file lock for its persisted Ghidra project, preventing
concurrent resumption. `Ctrl+C` cancels only the unique transient container.
The stopped container is inspected before removal for its real `OOMKilled`
flag; evidence and logs are preserved.

`ANALYSIS_TIMED_OUT` still detects a saved partial analysis even with process
exit 0. `GHIDRA_COMPLETED` requires a logged Ghidra analysis success and save
plus exit 0; it means *reported* completion, not verified individual-analyzer
correctness. `PARTIAL` covers absent completion evidence, cancellation returns
`CANCELLED`, and genuine OOM returns `OOM`.

To continue the previously saved ELF project without re-importing:

```bash
cd ~/projects/claude
kitt re lab analyze --path 2.1.308 --resume-job job-27ee75656097 --dry-run
kitt re lab analyze --path 2.1.308 --resume-job job-27ee75656097
```

Resume calls Ghidra's documented `-process`, preserving the original
`KittAnalysis.gpr`/`KittAnalysis.rep` and creating distinct per-run logs.
New jobs have SHA256 input manifests. Jobs before 0.88.75 still use
an operator-selected import/save receipt instead of historic cryptographic
identity proof. The original binary is verified before and after execution.
The source recovery / JavaScriptCore extraction stage is a separate task.

The Ghidra project is the native analysis result. This command does not
automatically reconstruct original source code, export pseudocode for every
function, or complete Bun/JavaScriptCore extraction. Operator guest
attestation and code recovery must be verified separately.

## Host source staging for networkless Linux RE

The managed `kitt-re-full` has `--network none`, and the Linux guest cannot
clone GitHub repositories directly. An operator with host network access can
stage the official Wine-Staging patch repository in the bind-mounted workspace:

```bash
kitt re lab stage-wine --approve
kitt re shell linux
ls /workspace/sources/wine-staging
```

This command runs Git only on the host, requires an interactive operator
terminal, checks the managed Linux workspace and refuses to replace existing
source trees. It reports the exact Git commit and exposes the same source under
`/workspace/sources/wine-staging`. It does not stage Wine upstream itself,
apply patches, enable guest network, authenticate GitHub, or grant model access
to arbitrary guest processes. The Wine-Staging repository is a patch collection;
a comprehensive DLL stub inventory also requires a compatible upstream Wine
source checkout.

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
