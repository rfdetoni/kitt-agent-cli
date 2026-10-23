## Security and lifecycle changes in 0.86.10

The noVNC endpoint is **HTTP Basic-authenticated**, including WebSocket upgrades
from other containers on the bridge. The random per-VM password is stored in
an owner-only file; retrieve it explicitly:

```sh
kitt vm container credentials --profile ~/.local/share/kitt/vm-containers/windows-podman/profile.json
```

The runner starts by its immutable `sha256:` image ID (recorded in the VM
profile). Container startup is considered successful **only after** the
authenticated noVNC endpoint responds, and a second simultaneous start is
blocked. Stopped containers retain accessible logs; when restarting, the
previous run logs are archived as `previous-run.log` in the VM directory.

Take an offline baseline **after OS setup**, while the guest is shut down:

```sh
kitt vm container snapshot --profile /path/to/profile.json --action save --name clean
kitt vm container snapshot --profile /path/to/profile.json --action list
kitt vm container snapshot --profile /path/to/profile.json --action restore --name clean --yes
```

Snapshots copy the guest QCOW2 and available UEFI/TPM state. A large disk
requires sufficient free host storage. Disk restoration is staged and
replaces the original; UEFI/TPM restoration requires the same guest to be
stopped and should be protected by an additional external backup for
critical data.

**Optional probe channel:** provide `--probe-port 18008` to `kitt vm create`
to expose guest SSH only at host `127.0.0.1:18008`, using QEMU user
`restrict=on` networking (guest egress blocked). Without that flag the guest
has **no network interface**. This port is not a shell authorization: install
and configure an OpenSSH forced-command `guest_rpc` endpoint and pin
host identities yourself before using it. The broker uses the existing
authenticated SSH differential probe contract:

```sh
kitt vm lab doctor --hosts /path/to/host-guests.json
kitt vm lab run --workspace /path/to/authorized-workspace \
  --hosts /path/to/host-guests.json --mission /path/to/authorized-workspace/mission.json
```

The lab broker does **not** give the agent a general VM shell or network
access. The operator still has to provision SSH guest accounts, trusted
host keys and signed/pinned probes. Automated guest OS setup and a real
cross-guest KVM lab acceptance run remain separate requirements.

**Wizard behavior:** `kitt vm create` now asks native/Docker/Podman in
interactive use; `--distro` and `--tools` are rejected with a container
backend because these options do not install RE tools inside arbitrary
Linux guest ISOs. Use `kitt vm container toolbox --profile full` separately.

**Migration:** profile schema has changed in 0.86.10 (immutable runner image
ID, SSH probe port and viewer secrets). VMs created with 0.86.8 should
be exported/backed up while stopped and recreated using the new wizard;
do not edit JSON and guess missing secrets.
# QEMU VMs in Docker/Podman and prebuilt Linux RE images

**Agent 0.86.10 (optional)**. These are two distinct execution modes: a QEMU VM running *inside* a container, and a Linux **toolbox container** with tools already installed. Neither replaces the ordinary KITT coding agent or its native QEMU supervisor.

## Container images

| Image | Purpose | Included tools |
| --- | --- | --- |
| `kitt/qemu-vm:0.86.10` | QEMU guest runner with web browser noVNC | QEMU, OVMF UEFI, TPM 2.0, noVNC |
| `kitt/linux-re:base-0.86.10` | Linux RE toolbox | GDB, LLDB, clang, GCC, binutils, strace, ltrace, Python, Git, Java, CMake |
| `kitt/linux-re:wine-0.86.10` | Linux RE toolbox | Base plus Wine64 and MinGW |
| `kitt/linux-re:full-0.86.10` | Linux RE toolbox | Wine plus Frida 17.23.3, Frida Tools 14.11.0, Ghidra 12.1.4 and REA 6.4.0 (Node 24.11) with SHA-256 verified |

**Important:** the RE toolbox runs directly as a container. Its tools do **not** appear automatically in an arbitrary Linux guest ISO. Use a disposable QEMU guest for binaries that might be hostile. An installed QEMU guest OS is separate from the container's Debian userspace.

## Requirements

Linux x86_64, Docker Engine or Podman, enough memory/storage. Accessible `/dev/kvm` is recommended; a rootless Podman account may need group access to KVM. Container images must be built/pulled explicitly. No `--privileged`, Docker socket mounts or automatic root escalation are used.

## Build locally (works without a published registry)

```sh
kitt vm container doctor --runtime podman
kitt vm container build --runtime podman --image qemu
kitt vm container build --runtime podman --image toolbox --profile base
kitt vm container build --runtime podman --image toolbox --profile wine
kitt vm container build --runtime podman --image toolbox --profile full
```

Replace `podman` with `docker` as needed. Builds fetch official distro packages and (in full) an SHA-256-verified Ghidra archive. Large images and build times are expected. Dockerfiles are shipped inside the Agent package.

Release CI is configured to publish prebuilt image tags to GHCR after merging to main. When published and accessible, use the explicit pull commands instead of building locally:

```sh
kitt vm container pull --runtime podman --image qemu
kitt vm container pull --runtime podman --image toolbox --profile full
```

If images are private or publishing failed, pull returns an error; use local build. Publishing is **not** assumed successful just because the workflows exist.

## Create a Windows or Linux QEMU VM inside Docker/Podman

```sh
kitt vm create --os windows --backend podman --source "$HOME/ISOs/Windows11.iso" --memory-mib 4096 --cpus 2 --disk-gib 64 --port 18006 --yes
kitt vm create --os linux --backend docker --source "$HOME/ISOs/debian.iso" --memory-mib 2048 --cpus 2 --disk-gib 32 --port 18007 --yes
```

The existing interactive `kitt vm create` wizard also accepts `--backend docker` or `--backend podman`. It asks which OS and which ISO/QCOW2 you own. The source is not modified. For QCOW2, backing files/external data are rejected; the disk is copied into a private state directory.

Start the Windows ISO installer (or Linux ISO installer with the corresponding profile):

```sh
kitt vm container start --profile ~/.local/share/kitt/vm-containers/windows-podman/profile.json --install
```

Open **http://127.0.0.1:18006/vnc.html**. Complete your Windows/Linux installation through the browser, shut it down, then restart *without* `--install`:

```sh
kitt vm container start --profile ~/.local/share/kitt/vm-containers/windows-podman/profile.json
kitt vm container status --profile ~/.local/share/kitt/vm-containers/windows-podman/profile.json
kitt vm container logs --profile ~/.local/share/kitt/vm-containers/windows-podman/profile.json
kitt vm container stop --profile ~/.local/share/kitt/vm-containers/windows-podman/profile.json
```

No OS image is downloaded, Tiny11 is not generated and Windows licensing remains your responsibility. No QEMU NIC is attached to the Windows/Linux guest: `-nic none` is hardcoded in the image. The container uses bridge networking only to expose the browser viewer on **127.0.0.1**, so the *container itself* may have network egress; apply host firewall rules if needed.

noVNC has no additional password, so do not expose or proxy this port publicly without independent authentication. State is persistent and must not be confused with a disposable snapshot. User-supplied ISO is mounted read-only; the container cannot see arbitrary host files.

For hosts with no accessible `/dev/kvm`, add `--allow-tcg` explicitly to start in very slow emulation mode. Podman rootless uses `--userns=keep-id` and group preservation where supported. Failure to access KVM is not handled through automatic privilege escalation.

## Run the prebuilt RE toolbox

```sh
kitt vm container toolbox --runtime podman --profile full --workspace "$HOME/projects/trusted-binaries"
```

It starts a shell with no network, dropped capabilities and the explicit workspace mounted at `/workspace`. For trusted files only: these containers do not provide a VM-strength isolation boundary.

```sh
gdb --version
clang --version
frida --version
/opt/ghidra/support/analyzeHeadless
```

## Limitations

CI covers argument/permission boundaries, shell syntax and image build. Real Docker/Podman QEMU Windows boots, KVM and rootless Podman behavior on your hardware are separate acceptance gates. This backend does not yet automate VM snapshots, export a verified source patch from the guest, configure Windows SSH runners, or install its RE toolbox tools inside an arbitrary Linux ISO guest.

See [native VM guide](VM_CREATION_HELPER.md) and [VM security model](VM_AUTONOMY_SECURITY.md).

## REA in the full toolbox

The `full` profile now includes the published `rea-agents@6.4.0` CLI and a compatible Node.js 24 runtime, installed at build time. No Node/REA dependencies are added to the lighter `base` or `wine` profiles, nor to KITT's ordinary coding agent. Verify the installed image by running `rea --version` and `rea doctor --provider ghidra --json` **inside** a full toolbox shell. `GHIDRA_INSTALL_DIR` points at the pinned Ghidra in the container.

For host-side governed MCP, install REA independently and explicitly enable `kitt-rea`; the toolbox executable is not automatically reachable from the host's MCP registration. See [REA integration](REA.md) for native, JavaScript/Electron, .NET and evidence comparisons. Toolboxes are containers, not a safe sandbox for executing untrusted binaries.


## Reuse installer-managed Docker RE lab without extra QEMU guests

The --with-re-lab installer already creates kitt-re-full (Linux RE toolbox)
and kitt-re-win11 (Dockur Windows). These are separate from QEMU ISO guests.

Read-only: kitt re status; kitt re setup linux; kitt re setup windows
Explicit operator lifecycle: kitt re start linux; kitt re stop windows
Human terminal in existing toolbox: kitt re shell linux

Discovery checks labels, expected private mounts and container identity.
Supplying --source still selects the QEMU VM flow.
A Dockur console is not an authenticated probe oracle: oracle_ready stays
false until a separate forced-command channel is established.
Docker discovery never enables KITT's unattended guest autonomy.


## Specialized RE executors (Agent CLI 0.88.77)

Use `kitt vm container build --image ghidra|frida` or
`kitt vm container pull --image ghidra|frida` to install the independently
versioned Ghidra and Frida images. The `full` toolbox no longer installs
those two tools. Ghidra runs noninteractive via `kitt re lab analyze`;
Frida's interactive tool shell is operator-only through
`kitt vm container toolbox --profile frida --workspace DIR`. Add
`--allow-ptrace` only when isolated local instrumentation needs it.
This does not provide remote attachment to Windows; instrument inside its VM.
No backward-compatibility image fallback is provided.
