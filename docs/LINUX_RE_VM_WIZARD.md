# Linux reverse-engineering VM wizard — user-provided distribution media

This is an optional, operator-owned Linux guest for reverse engineering,
**separate from the Debian Minimal KITT coding-agent VM** managed by
`kitt vm run`. The wizard never downloads a distro image, executes setup
on the host, or modifies the supplied ISO/QCOW2.

## Supported distributions and profiles

The setup wizard accepts Debian, Ubuntu, Kali, Fedora, Arch or `other`.
Selection records the distribution and generates a guest-local setup plan;
it does not claim to identify the operating system from ISO contents.

| Tool profile | Purpose |
| --- | --- |
| `minimal` | Bare distribution; install tools later as needed |
| `re` | C/C++ toolchain, GDB, binutils, clang/LLDB, strace, Java, Python |
| `wine` | RE baseline plus Wine for Windows compatibility experiments |
| `full` | Wine + RE baseline; Ghidra and Frida handled separately |

Ghidra and Frida have version-sensitive installation requirements and
are **not** silently installed. The generated `setup-re-tools.sh` is a
human-reviewable example for the chosen distro. It remains on the host;
copy its contents into the installed guest through a trusted mechanism
and run it as root **only inside the isolated VM** after reviewing.

## Interactive wizard

```sh
kitt vm linux prepare
```

Prompts collect source ISO/QCOW2 path, distro, tool profile, RAM, vCPUs,
disk capacity (for ISO install), destination and final confirmation.
By default it creates `~/.local/share/kitt/vm/linux-re` with owner-only
permissions, a private JSON profile, independent QCOW2 disk and an
installation script. The supplied source image is never modified.

## Fully scripted VM creation

```sh
kitt vm linux prepare \
  --source ~/ISOs/debian-re.iso --kind iso --distro debian \
  --tools wine --memory-mib 4096 --cpus 2 --disk-gib 64 \
  --destination ~/.local/share/kitt/vm/linux-re --yes
```

Already-installed Linux guest images can be imported as standalone QCOW2
by using `--kind qcow2`; backing chains and external data files are denied.
The source disk is copied instead of being edited.

## Install and boot

```sh
kitt vm linux install \
  --profile ~/.local/share/kitt/vm/linux-re/profile.json
```

Connect a VNC client to **127.0.0.1:5902** and complete the distribution's
normal installer. The install ISO is read-only. Network interfaces are
disabled by default. Later start the already-installed disk without ISO:

```sh
kitt vm linux start \
  --profile ~/.local/share/kitt/vm/linux-re/profile.json
```

For **temporary, explicitly authorized package setup only**, add
`--network` to `install` or `start` for user-mode QEMU NAT networking.
Reboot without `--network` to conduct experiments on potentially unknown
programs. Do not expose host credentials or writable host shares.

## Security and limitations

- Linux guests run offline by default with no mounted host checkout,
  no SSH agent forwarding and only loopback VNC access.
- User-approved package installation inside the guest is separate from
  VM preparation; no distro-specific installer is silently automated.
- This is **not** a cryptographically attested Windows/Wine experimental
  oracle. For untrusted binaries, clone a trusted baseline and use disposable
  snapshots. The initial wizard disk is mutable so you can install an OS.
- Scripts do not claim guaranteed package availability across distribution
  versions, nor do they silently download Ghidra/Frida from unverified URLs.
- The dedicated Linux RE guest does not yet expose a verified remote API to
  the autonomous Agent; integration requires a guest runner and real VM tests.
- CI tests parser, input restrictions, QEMU network defaults and bootstrap
  script construction. No real Debian/Kali/Windows QEMU VM boot has been
  performed in this change.

See [Windows VM wizard](WINDOWS_VM_WIZARD.md) for the counterpart reference
VM and [VM supervisor](VM_SUPERVISOR.md) for the separate KITT agent guest.
