# Agent CLI 0.88.10 — guarded offline Clean-Room RE pipeline

- `kitt re cleanroom build-image`: creates a locally SHA-addressed model/llama-cli container without network pulls or downloading models.
- `kitt re cleanroom run`: sequences approved spec handoff, independently signed offline implementation, host-checked DLL-only Git changes, separately signed offline test plan, and hash-chained audit receipts.
- An implementer uses one local GGUF generation only and sees bounded excerpts from a clean Wine checkout, not the analyst's observations beyond the approved contract.
- Reserved `spec-extractor` and `wine-implementer` personas are blocked from retained-agent shared sessions; invoke the isolated RE CLI instead.
- Regression tests exercise denied cross-module file changes, unsigned test plans, forbidden retained children, and validator tampering.
- Packaged Dockerfile, agent entrypoint and builder support Docker or Podman with root-owned review-key verification and immutable OCI image IDs.

**Fail-closed limitations:** the operator must supply trusted base images, local GGUF/model binaries, a root-owned public review key and detached reviewer signatures, authorized Windows guest and Wine checkout. A green validator result is `NEEDS_INDEPENDENT_ORACLE_CONFORMANCE` (nonzero CLI exit). The existing fixed Windows/Wine event pilot does not verify newly generated patches. No Windows or Docker E2E execution is claimed by this release; no automatic general-purpose proprietary probe runner is authorized.
