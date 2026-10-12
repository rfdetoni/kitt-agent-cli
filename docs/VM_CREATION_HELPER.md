# KITT VM creation helper — Windows and Linux

The optional command `kitt vm create` provides a single interactive entry
point to create offline QEMU Windows and Linux reverse-engineering test VMs.
It does not affect `kitt` or `kitt -p`, and does not install guest operating
systems automatically. Media must be supplied by the operator.

## Guided terminal setup

```sh
kitt vm create
```

The helper asks which operating system to prepare (`1` Windows or `2` Linux),
then requests an absolute or `~/...` path to an existing `.iso` or `.qcow2`.
It checks that the file exists and is not a symlink, and passes the file
to the corresponding existing wizard:

- **Windows**: choose edition label (`official`, `tiny11`, `tiny11-core`,
  `custom`), RAM, CPU count, disk size and destination.
- **Linux RE**: choose distribution (`debian`, `ubuntu`, `kali`, `fedora`,
  `arch`, `other`), tool profile (`minimal`, `re`, `wine`, `full`), RAM, CPU,
  disk size and destination.

After confirmation the tool creates the VM's private state, disk and profile
and prints the path plus a follow-up install command. The original ISO/QCOW2
is **not modified or deleted**. VM installation from ISO remains interactive.

## Use with scripts

```sh
kitt vm create --os windows --source ~/ISOs/Windows11.iso \
  --variant official --memory-mib 4096 --cpus 2 --disk-gib 64 --yes

kitt vm create --os linux --source ~/ISOs/debian.iso \
  --distro debian --tools wine --memory-mib 4096 --cpus 2 --disk-gib 64 --yes
```

`kitt vm wizard` is an alias for `kitt vm create`. In noninteractive mode
`--os`, `--source` and `--yes` are required to avoid accidental prompts.
`--kind` can be supplied explicitly, but must match `.iso` or `.qcow2`.
Crossing Windows-only `--variant` with Linux-only `--distro` or `--tools`
is rejected.

## Install and start the created guests

```sh
kitt vm windows install --profile ~/.local/share/kitt/vm/windows-oracle/profile.json
kitt vm linux install --profile ~/.local/share/kitt/vm/linux-re/profile.json
```

The Windows guest uses local-only VNC `127.0.0.1:5901` and Linux
`127.0.0.1:5902`. They are offline by default. Only the Linux wizard
supports temporary opt-in NAT via `--network` for guest package installation.

## Notes and limitations

- The helper delegates QEMU resource checks to the specialized wizards.
- It does not download Windows, Tiny11 or a Linux ISO. An existing QCOW2 is
  copied and must be standalone without external backing files.
- QEMU, swtpm/OVMF (for Windows) and a VNC client must already be installed.
- Real VM boot and guest package preparation require validation on hardware.
- This is separate from the unattended Debian agent VM and does not
  grant a guest access to the host workspace or credentials.

See [Windows wizard](WINDOWS_VM_WIZARD.md) and
[Linux RE wizard](LINUX_RE_VM_WIZARD.md).
