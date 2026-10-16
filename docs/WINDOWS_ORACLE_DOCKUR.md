# Disposable Windows 11 Oracle via Dockur (Agent CLI 0.88.6)

The next KITT installer can bootstrap Windows 11 and its probe transport on
**first boot** without manual OpenSSH, Python, SDK or RDP configuration in the
guest. It uses Dockur /oem install.bat and a native Windows PowerShell
guest_rpc implementing the same bounded version-1 probe.info/probe.run contract.

## Fast path: existing disposable lab (DESTROYS its Windows disk)

The operator must explicitly permit destruction and confirm Windows licensing.
The host's KITT-managed Linux toolbox and RE workspace are retained.

    kitt re oracle bootstrap --recreate --yes --accept-license

Or from the root installer after updating its own version:

    ./install.sh --preset full --channel release --with-re-lab \
      --re-runtime docker --re-oracle-bootstrap --re-oracle-recreate \
      --accept-windows-license --yes

A new lab with no existing Windows guest does not need --recreate. Without
--recreate an existing Windows guest is never deleted, reset or modified.

## What the bootstrap does

1. Verify the KITT-managed Linux RE toolbox and its private Docker labels.
2. Use the MinGW compiler in that toolbox to build KITT's fixed benign
   event-synchronization PE from oracle_assets/event_probe.c. No arbitrary
   operator file, user-provided command or external sample is executed.
3. Generate unique SSH Ed25519 client and Windows host keys in the private
   RE lab (mode 0700/0600). Pre-pin the host public key to known_hosts.
   Separate client and Windows host private keys are never printed.
4. Create Windows OEM files including manifest.json with the actual binary
   SHA-256, a fixed PowerShell runner and OpenSSH first-boot install script.
5. Check Dockur image availability *before* removing any existing guest.
   If --recreate was explicitly confirmed, stop and remove only the validated
   KITT-owned Windows container by immutable ID, then delete only its validated
   lab/windows storage. The Linux toolbox and other user files remain intact.
6. Create Dockur from its local pinned image, read-only mount OEM, and publish
   only 127.0.0.1:8006 (console) and 127.0.0.1:2222 (Windows SSH).
   The USER_PORTS=22 environment variable supports Dockur user-mode fallback.
7. Windows unattended setup runs C:\OEM\install.bat. It installs the Microsoft
   OpenSSH.Server optional feature, creates the nonadmin kittprobe user with
   a random non-disclosed password, installs the locked-down SSH host key,
   configures key-only sshd and ForceCommand, and places the fixed launcher
   and manifest in C:\KittOracle. Windows firewall limits SSH to the guest
   management subnet. No administrative or interactive model shell is granted.
8. The host's private oracle/hosts.json and oracle/ssh_config pre-pin
   StrictHostKeyChecking and the client key. The expected manifest hash is
   stored separately in oracle/manifest.sha256.

OEM setup may require Windows Features-on-Demand / Microsoft Update access.
If the image, compiler, network, Windows license, OpenSSH capability, guest
firewall or first-boot script fails, report BLOCKED_ENV. There is no fallback
to unrestricted remote PowerShell or bypass of SSH host-key validation.

## Check real readiness

When the Windows console shows the installed desktop and bootstrap.status
exists inside C:\KittOracle, run with the actual paths/hashes from bootstrap:

    kitt re oracle doctor --hosts /ABSOLUTE/PRIVATE/hosts.json \
      --manifest-sha256 ACTUAL_64_CHARACTER_SHA256
    kitt re status --check-oracle --hosts /ABSOLUTE/PRIVATE/hosts.json \
      --manifest-sha256 ACTUAL_64_CHARACTER_SHA256

The doctor validates Docker identity, SSH host key and public-key login,
Windows x64, the SHA of the in-guest manifest and the hashes of registered
executables. It is read-only: no probe executes during doctor.

A successful handshake is not hypervisor attestation: guest_attested=false.
The guest result is not globally cached and must be revalidated on use.

## First test and Wine extension

The Windows guest runner accepts ONLY probe.info and probe.run with a
registered hash-pinned probe name. The first registered probe,
event_auto_reset, is KITT-authored, short-lived and tests CreateEventW,
WaitForSingleObject and SetEvent in a process without external side effects.
The Linux RE toolbox provides MinGW and Wine for this same binary. The toolbox
is a container, not a VM isolation boundary; do not execute unknown binaries
inside it. A full autonomous differential mission still requires configuring
a separate approved Linux+Wine guest and the existing mission broker, or an
operator-invoked bounded comparison that explicitly documents its isolation.

## Safety and support

- Never assume running == oracle_ready.
- Never embed Windows passwords, SSH private keys or licensed Windows media
  in Git. Keys and the staged OEM private host key reside only in the private
  local operator-owned lab. The Windows first-boot script removes its staged
  host-key copy from C:\OEM after successful configuration.
- This is an explicitly **disposable test VM**, not a production SSH setup.
- No internet-facing RDP/SSH, Docker socket mounts, anonymous SSH,
  AcceptAnyHostKey, or dynamic executable selection.
- This feature does not override KITT approvals or enable auto use by a model.
- The host console remains http://127.0.0.1:8006.
- To preserve the current Windows OS, do not pass --recreate and do not use
  --re-oracle-recreate in the root installer.

If OpenSSH provisioning is blocked because Windows Features-on-Demand isn't
available, provide the signed Microsoft FoD source to Windows through the
operator's approved installation process. Never silently disable security.

## Operator-only fixed differential pilot

Once doctor reports READY, execute exactly the fixed KITT-authored PE on the
Windows reference and the existing no-network full Wine toolbox:

    kitt re oracle pilot \
      --hosts /ABSOLUTE/PRIVATE/hosts.json \
      --manifest-sha256 REAL_64_CHARACTER_SHA256 \
      --yes

The host validates the current Windows Oracle readiness again, verifies the
local PE SHA against the approved manifest, runs only event_auto_reset (no
arbitrary path/commands/arguments) and compares the Windows receipt against the
Wine result. A bounded evidence report is saved in the private lab as
oracle/pilot.json. Codes: 0 PASS_SCENARIO, 1 FAIL_SCENARIO, 2 BLOCKED_ENV.

This initial pilot is safe for its own benign test binary, but the Linux
toolbox uses a shared kernel and is not suitable for untrusted executables.
Full mission execution with multiple guest VMs still needs the existing
operator-approved forced-command host brokers and verified isolation.
