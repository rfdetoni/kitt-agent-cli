# Agent CLI 0.88.21 — do not reset allow-all on daemon attach

- At daemon attach, the CLI reads `runtime.get_autonomy` and synchronizes its local state and visible modal to the daemon's persisted policy. It never sends a fresh `runtime.set_autonomy` from stale frontend state.
- An intentional `/autonomy allow-all` or explicit modal selection still invokes `runtime.set_autonomy` and requires a positive daemon response before updating the local state.
- Existing command, network, control-plane and sandbox restrictions remain governed by the host; allow-all is not a capability escalation.
- Includes daemon-attach regression. The assistant runtime must implement `runtime.get_autonomy` to report effective policy.
