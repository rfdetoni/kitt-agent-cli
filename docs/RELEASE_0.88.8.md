# Agent CLI 0.88.8 — private fixed-probe build/staging

Security correction to 0.88.7:

- Compile authored Win32 probe in a disposable, restricted Linux RE container
  using a source file materialized under the private operator-owned Oracle root.
  No source or compiler output is read from the model-writable workspace.
- Copy the operator-pinned PE into a fresh isolated Wine container's read-only
  mount containing only that binary. Never mount the OEM directory with the
  Windows SSH host private key.
- Verify the staged candidate SHA-256 before starting Wine.
- Retains official Dockur, scoped reset, forced-command SSH and the existing
  pilot receipt contract. Windows/KVM runtime E2E remains UNVERIFIED.
