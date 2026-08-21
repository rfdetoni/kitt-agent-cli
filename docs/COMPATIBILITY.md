# KITT Agent CLI — Compatibility Policy

## Pre-1.0 Development (Current Phase)

K.I.T.T. Agent CLI is in active local-first pre-1.0 development (`0.x.y`).

### Guarantees during Pre-1.0:
- **Internal APIs**: Subject to breaking changes without deprecation cycles.
- **Local State Schema**: May be reset across minor pre-1.0 increments. If an incompatible state is detected, run `kitt doctor --reset-state`.
- **Daemon & Wire Protocols**: Versioned explicitly via `DAEMON_PROTOCOL_VERSION`. Mismatched clients/daemons fail fast.
- **Tool Surfaces**: Canonicalized on single model-facing tool runtime (`kitt_runtime`).

## Post-1.0 Releases

Following release `1.0.0`, KITT will strictly adhere to Semantic Versioning (SemVer 2.0.0):
- **MAJOR**: Incompatible API, protocol, or state schema migrations.
- **MINOR**: Backwards-compatible features and new operation capabilities.
- **PATCH**: Backwards-compatible bug fixes and security hardening.
