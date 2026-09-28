# Contributing to K.I.T.T. Agent CLI

KITT is a local-first coding-agent control plane. Contributions should preserve the security boundary, bounded-context ownership and small deterministic hot path before adding new abstractions.

## Development environment

Requirements:

- Python 3.14+
- Git
- Rust only when validating an ecosystem companion that owns Rust code

Install the Agent development environment:

```bash
python -m pip install -e '.[dev]'
```

Run the baseline gates:

```bash
python -m compileall -q kitt tests
python -m pytest -q
python packaging/verify_cleanroom.py
```

Rust formatting, Clippy, Rust tests and native wheel construction belong to `rfdetoni/kitt-toolbox`. Assistant daemon/remote validation belongs to `rfdetoni/kitt-assistant`; durable semantic memory belongs to `rfdetoni/kitt-memory`; shared wire contracts belong to `rfdetoni/kitt-protocol`.

## Architecture rules

Use the bounded contexts in [docs/DOMAIN_ARCHITECTURE.md](docs/DOMAIN_ARCHITECTURE.md).

Inside the Agent:

1. domain concepts must not depend on UI, provider or persistence implementations;
2. application/orchestration code coordinates domain behavior and ports;
3. infrastructure adapters implement filesystem, SQLite, provider, MCP, browser and native concerns;
4. TUI code is presentation only and must not become a second runtime authority;
5. cross-repository contracts go through `kitt-protocol` or an explicitly versioned public interface;
6. do not duplicate memory ownership, provider routing, policy or execution state in another layer.

DDD is a tool, not a directory quota. Do not create aggregates, repositories or services for concepts that are simply infrastructure records.

## Coding style

- Prefer KISS, DRY and YAGNI.
- Standard library first.
- Keep functions/classes single-purpose.
- Prefer immutable value objects for identity/configuration contracts.
- Keep I/O at adapters/boundaries.
- Do not hide degraded behavior behind silent fallback.
- Keep output, searches, retries, concurrency and model context bounded.
- Never weaken approval, path-containment, capability or secret-handling rules for convenience.

## Pull requests

A PR should explain:

- the user-visible or architectural problem;
- why the change belongs to this repository;
- compatibility impact on sibling KITT components;
- tests and validation executed;
- any protocol/schema/version impact;
- benchmark evidence for performance claims.

Changes to `kitt-protocol`, wire schemas, shared memory contracts or installer composition require explicit cross-repository compatibility validation.

## Clean-room rule

When learning from external projects, reimplement ideas independently. Do not copy protected source, names, internal APIs or repository-specific identifiers. The production-source clean-room guard must continue to pass.

## Versioning

The project is pre-1.0 and follows the compatibility policy in [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md). Feature additions normally advance the minor version; compatible fixes advance the patch version. Published tags remain authoritative.

## Documentation

When changing runtime operations, update or regenerate [docs/RUNTIME_REFERENCE.md](docs/RUNTIME_REFERENCE.md):

```bash
python scripts/generate_runtime_reference.py > docs/RUNTIME_REFERENCE.md
```

When changing TUI behavior, update [docs/TUI_ARCHITECTURE.md](docs/TUI_ARCHITECTURE.md) and [docs/ACCESSIBILITY.md](docs/ACCESSIBILITY.md) when applicable.
