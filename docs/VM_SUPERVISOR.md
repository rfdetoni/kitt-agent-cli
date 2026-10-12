# Disposable VM autonomy — QEMU supervisor (experimental)

## What is implemented

`kitt vm doctor` checks the operator-owned profile, pinned standalone qcow2
image and installed QEMU/OpenSSH tools. `kitt vm run` boots a new disposable
QEMU snapshot, opens **one loopback SSH forward** and optionally exposes one
host-owned model gateway. The VM is destroyed in `finally` on success/error.
Code runs *inside the guest*. No guest writes directly to the host checkout.

## Preconditions / trust boundary

- Linux host with QEMU system x86_64, qemu-img and OpenSSH client installed.
- Dedicated Linux VM image provisioned by the operator. The guest must already
  contain Python 3.14+, compatible KITT, a non-root SSH account and a private
  disposable project workspace. Disable the guest KITT daemon; the worker does
  this when launching each contract.
- Configure sshd authorized_keys for that account with a forced command:

    restrict,command="python3 -m kitt.vm_worker --workspace /home/kitt/workspace" ssh-ed25519 AAAA... kitt-lab

  Use an actual dedicated SSH public key instead of `AAAA...`.
  The worker accepts one newline-terminated JSON goal, runs a KITT durable
  contract with `--contract --contract-yes --allow all`, then returns a bounded
  result. `--allow all` is **not a bypass** for every KITT internal safeguard;
  it supplies broad project capabilities and removes repeated confirmations
  that can be safely covered by the VM boundary.
- Maintain a private SSH identity file and pinned guest known_hosts file.
  Both must be owned by the host user with mode 0600.
- The VM base image must be a standalone, unencrypted qcow2, with no backing
  file or external data file. Record its complete SHA-256 independently.
- No host SSH agent, personal credentials, VM management socket, direct source
  mounts, USB/PCI passthrough, or external network access is provided.

## Configuration

Create `~/.kitt/vm/profile.json` (chmod 600):

```json
{
  "version": 1,
  "image": "/home/operator/vm/kitt-agent.qcow2",
  "sha256": "REPLACE_WITH_ACTUAL_64_CHARACTER_SHA256",
  "ssh_key": "/home/operator/.ssh/kitt-vm",
  "known_hosts": "/home/operator/.ssh/kitt-known-hosts",
  "ssh_user": "kitt",
  "host_key_alias": "kitt-sandbox",
  "memory_mib": 4096,
  "cpus": 2,
  "timeout_seconds": 1200,
  "boot_seconds": 120,
  "gateway_port": 3000
}
```

Setting `gateway_port` to zero disables guest access to any host service.
When nonzero, QEMU forwards ONLY guest TCP `10.0.2.100:3000` to
`127.0.0.1:<gateway_port>` on the host. Configure the guest KITT model
provider URL accordingly. Restrict the gateway's credentials, conversation
data, token budget and outbound provider access separately; QEMU's forwarding
is not an authorization mechanism at the application layer.

Host VM networking uses QEMU user-mode `restrict=on,ipv6=off`, plus a single
loopback host-to-guest SSH forward. Avoid general NAT, bridge, vhost-net,
9p, virtiofs and host-provided writable drives. QEMU's `restrict=on` blocks
ordinary guest-to-host/outside traffic but explicitly configured forwarding
rules still work.

## Commands

```sh
python -m installer --preset agent --with-vm-lab --yes
kitt vm doctor --profile ~/.kitt/vm/profile.json
kitt vm run --profile ~/.kitt/vm/profile.json \
  --goal "Investigate and repair the Win32 event probe under Wine; run relevant tests"
```

Do not run `kitt vm run` from the guest itself. The command is the
**operator-owned host entry point**. Every run starts from the same clean
image using a QEMU snapshot overlay, and cannot modify the image on disk.
The output is a JSON report. A `PASS` result means the guest KITT durable
contract exited successfully; it is NOT host-side proof of behavioral
correctness. Host review and independent verification are still required
before applying a diff, pushing a commit, or releasing binaries.

## Limits

- The supervisor does not create a Windows VM image, auto-install Ghidra,
  distribute source code into the guest, or provision trusted guest images.
- It does not expose a general shell to the host KITT tool registry and
  does not automatically publish GitHub changes.
- QEMU snapshot cleanup has not been tested against a live hypervisor here.
- There is no cryptographic guest attestation, artifact promotion pipeline,
  VM image signing, memory-usage telemetry, or post-crash resource monitor.
- Guest startup relies on a prepared SSH server and clean guest image.
- Windows oracle / Linux-Wine comparative probes are handled by the separate
  operator-provisioned `reverse_engineering.mission` runner.

## Validation

`pytest -q tests/test_vm_supervisor.py` checks profile hardening, network
restriction and worker contract through mocks. Actual QEMU boot + SSH +
live KITT + Wine/Ghidra/Frida is an outstanding environmental gate.
Use a dedicated unprivileged Linux account to launch QEMU; kernel,
hypervisor and firmware must be kept patched. A VM is not a guarantee
against zero-day hypervisor escapes.
