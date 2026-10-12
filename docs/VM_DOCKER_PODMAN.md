# QEMU VMs in Docker/Podman and prebuilt Linux RE images

**Agent 0.86.8 (optional)**. These are two distinct execution modes: a QEMU VM running *inside* a container, and a Linux **toolbox container** with tools already installed. Neither replaces the ordinary KITT coding agent or its native QEMU supervisor.

## Container images

| Image | Purpose | Included tools |
| --- | --- | --- |
| `kitt/qemu-vm:0.86.8` | QEMU guest runner with web browser noVNC | QEMU, OVMF UEFI, TPM 2.0, noVNC |
| `kitt/linux-re:base-0.86.8` | Linux RE toolbox | GDB, LLDB, clang, GCC, binutils, strace, ltrace, Python, Git, Java, CMake |
| `kitt/linux-re:wine-0.86.8` | Linux RE toolbox | Base plus Wine64 and MinGW |
| `kitt/linux-re:full-0.86.8` | Linux RE toolbox | Wine plus Frida 17.23.3, Frida Tools 14.11.0 and Ghidra 12.1.4 with SHA-256 verified |

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
