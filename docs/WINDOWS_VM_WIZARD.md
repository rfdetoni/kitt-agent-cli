# Windows VM wizard — images supplied by the operator

Windows is **never downloaded or modified by the KITT installer**. The operator
provides an official Windows 11, Tiny11, Tiny11 Core or another Windows
installation ISO, or an already-installed standalone QCOW2 image.
Windows licensing and original media provenance remain the operator's
responsibility. A Tiny11 build is not an authoritative Windows API oracle;
use an unmodified Windows installation as the reference when validating Wine.

## One-time prerequisites

- Linux host with `qemu-system-x86_64`, `qemu-img`, `OVMF` and `swtpm`
  installed. KVM is recommended; QEMU can fall back to TCG.
- One user-provided `.iso` or unencrypted **standalone** `.qcow2`. The wizard
  never fetches Windows, runs tiny11builder, or downloads third-party images.
- 4–16 GiB RAM, 2–8 virtual CPUs, 32–256 GiB virtual disk for an ISO install.
- A local VNC client; this wizard binds **only to 127.0.0.1** (no public VNC).
  VNC has no password, so use a single-user host session; if connecting from a
  different machine, use an SSH tunnel rather than exposing port 5901.

## Guided setup

Run `kitt vm windows prepare` and enter the ISO/QCOW2 path when prompted.
The assistant displays the detected format and configured resources and
requires confirmation before creating the VM. Default destination:
`~/.local/share/kitt/vm/windows-oracle`.

Unattended preparation is also possible when the user explicitly supplies
the source image:

```sh
kitt vm windows prepare \
  --source /home/me/isos/Windows11.iso --kind iso --variant official \
  --destination /home/me/.local/share/kitt/vm/windows-reference \
  --memory-mib 4096 --cpus 2 --disk-gib 64 --yes
```

To create a Tiny11 VM, set `--source /path/to/tiny11.iso --variant tiny11`.
To import an already-installed machine, pass `--kind qcow2` and its source
disk path. The wizard **copies** the QCOW2 into a private VM directory;
backing images and external data files are rejected.

Custom UEFI templates can be supplied with `--ovmf-code` and `--ovmf-vars`
together. By default common Debian/Ubuntu OVMF paths are detected.

The wizard creates a private profile, writable QCOW2 disk (for ISO mode),
persistent UEFI variables and a private TPM 2.0 state directory. The source
ISO is never edited or deleted; its SHA-256 is recorded and checked before
starting the installation. The generated disk is dedicated to this Windows
VM and is not the Linux KITT agent image.

## Install Windows

```sh
kitt vm windows install \
  --profile ~/.local/share/kitt/vm/windows-oracle/profile.json \
  --vnc-display 1
```

Connect a VNC client to **127.0.0.1:5901** and complete Windows Setup.
Installation is interactive; no Microsoft account, edition selection or
license acceptance is automated. Shut down Windows normally when complete.
UEFI/TPM state and disk changes persist, but the Windows ISO remains
read-only.

Later boot the existing guest without the ISO:

```sh
kitt vm windows start \
  --profile ~/.local/share/kitt/vm/windows-oracle/profile.json \
  --vnc-display 1
```

`kitt vm windows` is separate from `kitt vm run`, which continues to manage
the autonomous **Linux agent** VM. The wizard does not grant the Windows
guest SSH keys, host directory mounts, LAN access or KITT autonomy.

## Security and limitations

- QEMU starts with `-nic none`: no guest network interfaces or port forwards.
- UEFI/TPM state is persistent so Windows can complete installation; do not
  use this mutable guest directly for running untrusted probes indefinitely.
  Take a trusted baseline and execute potentially dangerous samples only
  in an independently isolated, disposable copy.
- Neither Windows setup nor a real swtpm+OVMF boot has been executed in this
  CI. Regression tests cover CLI routing, path/size limits, read-only ISO,
  backed-disk rejection and offline QEMU argument construction.
- The Windows guest does not yet integrate with differential probe mission
  orchestration or a guest-agent installer. Those require separate
  provisioning and real tests before an agent can use it as an oracle.
- Use a legally obtained Windows image; KITT does not ship Windows binaries,
  modified OS files or a Windows product key.
