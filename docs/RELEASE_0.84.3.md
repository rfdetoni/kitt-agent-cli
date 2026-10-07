# K.I.T.T. Agent CLI 0.84.3 — stale Reverse Proxy control-plane refresh

## Root cause

Reverse Proxy 4.9.16 correctly accepts managed `log_level`, `log_content`, `log_file` and owner metadata. The remaining failure was process lifetime: the resident control plane can survive a package upgrade.

A 4.9.15 control plane reports the same schema-v1 health endpoint and accepts `service.start`, but its request decoder ignores the newer logging/owner fields. Agent CLI therefore believed the request succeeded while the old service manager wrote logs under its default `~/.kitt-reverse-proxy/logs` location.

## Fix

Before the first managed start or restart in an Agent process, `ReverseProxyClient` now:

1. asks the resident control plane to stop;
2. waits for its health endpoint to disappear;
3. starts/ensures the control plane from the currently installed `kitt-reverse-proxy` executable;
4. performs the managed operation through that refreshed process.

The refresh happens once per Agent process.

For managed starts with Agent logging configured, the Agent also reads the returned `logFile` and requires its parent directory to match the Agent log directory. A mismatched/missing path stops the just-created instance and fails explicitly.

Managed restart is implemented as stop + start with the current Agent logging configuration, so instances originally created by older control-plane versions migrate to the current log directory.

## Expected behavior

Running:

`kitt --log-level 2 --log-file ~/.kitt/logs/agent-cli.log`

and then starting a Reverse Proxy from KITT produces a dedicated Proxy log such as:

`~/.kitt/logs/reverse-proxy-chatgpt-3001.log`

The Agent and Proxy do not append to the same file.

## Compatibility

No Protocol, Memory, Reverse Proxy wire schema or daemon IPC change is required. Reverse Proxy 4.9.16 already contains the managed logging and owner fields; this release fixes Agent-side resident-process refresh after upgrades.
