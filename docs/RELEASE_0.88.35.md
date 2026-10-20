# KITT Agent CLI 0.88.35

- Automatically discover Bubblewrap candidates in `/usr/local/bin`, `/usr/bin`, `/bin` and every `PATH` directory; deduplicate symlinks, reject a binary resolving into the workspace and require an executable file.
- Require a safe `bwrap >= 0.12.0` **and** actual user/pid/ipc/uts namespace probe success. A broken binary or unsupported older package cannot hide a later functional version.
- Expose actionable, bounded namespace/AppArmor `uid_map` failure diagnostics to host `SandboxUnavailable`, `runtime.capabilities` and the agent tool description.
- Refresh positive and negative probe caches every 60 seconds, allowing newly installed binaries or repaired AppArmor profiles to be picked up without weakening isolation.
- Preserve the strong sandbox and policy gates; KITT does not disable AppArmor, make Bubblewrap setuid or elevate Linux capabilities automatically. The workstation owner must install a functional binary and, where necessary, an appropriately scoped AppArmor profile.

Validation: targeted pytest, Agent PR Checks, Docker/Podman and Windows packaging, plus synchronized Assistant and root ecosystem CI. Actual host AppArmor behavior must be confirmed with a local sandbox probe.
