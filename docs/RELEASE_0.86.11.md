# Agent CLI 0.86.11 — REA full-toolbox non-root runtime fix

- `kitt vm container toolbox` supplies an ephemeral writable `/tmp` and HOME/XDG cache/config paths for unprivileged UIDs.
- Prevents REA and Ghidra from attempting to write state into an inaccessible root-owned home, without exposing host credentials or HOME.
- Preserves `--network none`, dropped container capabilities, an explicitly mounted workspace, and image execution by immutable SHA-256.
- The prebuilt full toolbox image remains **0.86.10**, unchanged: REA 6.4.0, Node.js 24.11, Ghidra and Frida. No image rebuild or republishing is needed for this launcher-only patch.
- Adds a focused command-contract test. No changes to KITT MCP/agent programming authority.
