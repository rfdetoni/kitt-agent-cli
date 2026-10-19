# Agent CLI 0.88.15 — early SSH exit handling

Follow-up to 0.88.14: even when the SSH process rejects its connection before consuming its stdin request, `_ssh_exchange` catches only `BrokenPipeError`, waits for the real SSH exit code, reads the bounded stderr stream, and returns a sanitized failure classification. The host private key path and raw stderr are never included in user-facing diagnostics.

- Regression covers early exit on both write and close without a live SSH server.
- `pyproject.toml`, source-tree version fallback, and `uv.lock` are synchronized.
- Immutable Protocol SHA and other dependency pins are unchanged.
- Real Windows/KVM oracle acceptance remains operator-side and unverified by CI.
