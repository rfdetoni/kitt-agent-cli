# Autonomous reverse-engineering missions

This feature reduces repeated operator intervention after a **one-time, explicit lab setup**.
It does not grant the LLM administrator rights, uncontrolled shell access, arbitrary
remote PID attachment, or the ability to choose executable commands on guest hosts.

## Contract

- The model creates a workspace-local mission (JSON list of named probes) and requests the **read-only** MCP tool `lab_mission_plan`.
- The MCP tool inspects and validates the mission and an operator-owned SSH profile. It returns a fixed `argv`; it **does not execute** that argv.
- KITT must run that argv through its normal host-owned `process.run`/`run_command` policy and approval path. Workspace-scoped trusted autonomy may authorize this command, but the MCP server cannot grant itself execution permission.
- The mission controller uses two SSH aliases from the operator's private profile. It invokes no remote command strings: each guest SSH key **must** have a forced command executing `guest_rpc`.
- Each guest executes only the SHA-256-pinned local probe listed in its operator-provisioned manifest. The reference must report `Windows`, and the candidate must report `Linux+Wine`.
- Both guests are invoked concurrently per probe. Successful receipts are verified, archived to SQLite and compared. The controller returns exit status 0 = all match, 1 = divergent, 2 = blocked.
- A lost SSH connection or invalid receipt is **blocked**. No automatic replay of side effects, no fabricated match, and no change to Agent approval authority.

## One-time operator setup

1. Provision **separate disposable Windows and Linux/Wine VMs** under your control. Disable unnecessary network access and host mounts; keep SSH access on a restricted management network. Establish clean snapshot restoration externally. These scripts do **not** provision or roll back VMs.
2. Install the same compatible KITT Agent Python package in the guests, plus the Windows SDK / Wine as applicable. Build and distribute licensed test probes independently; record their SHA-256 in a private guest-local manifest.
3. Configure a dedicated SSH account/key for each guest with an **authorized_keys forced-command**, using `python -m kitt.extensions.builtin_plugins.reverse_engineering.guest_rpc --probe-root <guest-root> --manifest <guest-manifest>` on Windows, and the same command with `--wine-binary /usr/bin/wine` on Linux. Configure `no-agent-forwarding,no-port-forwarding,no-pty,no-X11-forwarding`. Do not allow an unrestricted interactive shell. The CLI cannot verify the forced-command restriction remotely.
4. Create a private OpenSSH config with aliases `windows-test` and `linux-wine`, pinned known_hosts and dedicated identities. Confirm `StrictHostKeyChecking=yes`, `BatchMode=yes`, and remote account isolation.
5. Create `~/.kitt/reverse-engineering/hosts.json` on the operator machine with mode **0600** and the content below. Do not put credentials, hosts config or private keys inside a model-writable repository.

    {
      "schema_version": 1,
      "ssh_config": "/home/operator/.ssh/kitt-lab-config",
      "reference": "windows-test",
      "candidate": "linux-wine"
    }

6. Enable the opt-in plugin by executing `kitt plugins enable kitt-reverse-engineering`, then restart KITT.

## Fully unattended mission flow

The agent writes a mission file under the current workspace, e.g. `.kitt/reverse-engineering/mission.json`:

    {
      "schema_version": 1,
      "probes": ["event_auto_reset"]
    }

Ask KITT to call `mcp.reverse-engineering.lab_mission_plan` with `{ "mission": ".kitt/reverse-engineering/mission.json" }`.
It produces approved, fixed argv for the mission controller. If the private operator profile is missing or invalid, it returns a blocked preflight instead.

The agent executes the suggested argv **through KITT's governed host runtime**. This is the only mutating entry point from the model's perspective. The host policy still controls execution. For repeat unattended missions, provide a narrowly scoped workspace trust policy and a dedicated VM lab; do not use a global shell allow-all override.

Operator-only direct form (outside MCP):

    python -m kitt.extensions.builtin_plugins.reverse_engineering.mission \
      --workspace /path/to/workspace \
      --mission /path/to/workspace/.kitt/reverse-engineering/mission.json \
      --hosts /home/operator/.kitt/reverse-engineering/hosts.json

After the run, KITT can query historical outcomes via `evidence_query`, inspect any divergences, propose source code changes and rerun the affected probes through the existing Plan → Execute → Validate → Retry system. The model cannot declare a mission verified if the subprocess exit status or receipt validator failed.

## Guest execution contract

Each SSH session sends exactly one UTF-8 JSON request line, for example:

    {"version":1,"method":"probe.run","probe":"event_auto_reset"}

The guest returns exactly one JSON object containing a hash-linked receipt and exits. Arbitrary command names, shell arguments, code, network targets and unregistered probes are rejected.

## Current limits and safety

- This feature is an **unattended orchestrator**, not VM lifecycle management or guest attestation. **VM isolation, snapshot restore, credentials, forced SSH commands, and Windows/Wine software installation are prerequisites**, not automated here.
- Hashes verify receipt consistency only; authenticated SSH with pinned host keys establishes the configured transport endpoint, not that the guest is uncompromised.
- Ghidra Headless and Frida adapters have standalone guest CLIs but are not yet mission-scheduled. Their use is opt-in, never via a general-purpose MCP command.
- Full autonomous Wine/ReactOS code correction is bounded by the existing KITT goals and verification; no self-declared success. Observations may contain private values: avoid capturing them in probes or importing unreviewed data.
- Only the lab's fixed probes are eligible. For each new API family, the operator must first provision and pin a valid test executable in both guests.
- Windows descendant process cleanup and snapshot restore are external guarantees: never run an untrusted test directly on a developer workstation.

## Validation

CI runs mission schema/permission tests, simulated reference/candidate parallel execution, SQLite receipt handling, and guest RPC rejection cases. Real SSH, Windows VM, Wine, Ghidra and Frida E2E must be separately verified on prepared hardware before promoting unattended execution to production.

## Dockur Windows Oracle (sem nova VM)

O container existente `kitt-re-win11` pode ser a referência Windows após
provisionamento explícito de OpenSSH/forced-command e manifesto de probes.
O diagnóstico sob demanda é `kitt re oracle doctor --manifest-sha256 SHA_REAL`
ou `kitt re status --check-oracle --manifest-sha256 SHA_REAL`.
Nenhum destes comandos instala servidor SSH, publica portas, inicia probes
ou altera as aprovações do KITT. As instruções completas e bloqueios estão em
[Windows Oracle Dockur](WINDOWS_ORACLE_DOCKUR.md).

## Fully automated disposable Dockur lab

The newer native Windows bootstrap uses Docker /oem and a Windows PowerShell guest RPC instead of requiring Python inside the Windows reference. Operator authorization, Windows licensing, and explicit consent to delete the current disposable Windows disk are still required. See [Windows Oracle Dockur](WINDOWS_ORACLE_DOCKUR.md). The existing SSH mission broker and manual QEMU guest flow remain supported; model approvals are not widened.
