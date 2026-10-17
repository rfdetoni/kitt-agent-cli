# Agent CLI 0.88.9 — bounded clean-room RE handoff

Adds a strict, operator-reviewed black-box API specification lifecycle:
- kitt re cleanroom validate / seal / verify
- kitt re cleanroom run-offline (operator approval required)
- private SHA-256 contract seal; no analyst history, tool stdout or code dumped into handoff
- fresh OCI offline implementer image pinned by immutable sha256 ID, no network, capabilities dropped, read-only root, separate Wine checkout and approved contract only
- explicit incomplete states: SEALED_NOT_EXECUTABLE, VERIFIED_NOT_EXECUTABLE, IMPLEMENTER_EXITED_UNVERIFIED, BLOCKED_ENV
- adversarial unit tests for contract and OCI invocation

Dependency: kitt-protocol 0.14.1 pinned to a concrete Git SHA. Version, Python lock and release metadata aligned.

The implementation does NOT attest a human reviewer's identity, certify an OCI image, provide an offline model image, prove source provenance, or complete real Windows/Wine end-to-end tests. Independently review the behavior specification and image, and treat missing OS isolation, runtime or guest Oracle as BLOCKED_ENV. No proprietary code, registers, offsets, or decompiled pseudo-code should flow to the implementation workspace.
