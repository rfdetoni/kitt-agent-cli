# K.I.T.T. Domain Architecture

KITT uses domain-driven boundaries where they clarify ownership. It does **not** force DDD patterns into presentation or infrastructure code.

## Ecosystem bounded contexts

| Context | Repository | Owns | Does not own |
| --- | --- | --- | --- |
| Agent Execution | `kitt-agent-cli` | task interpretation, goals, execution orchestration, policy integration, approvals, child coordination, session evidence | shared wire schemas, durable semantic-memory engine, browser provider implementation |
| Shared Contracts | `kitt-protocol` | versioned cross-process/domain contracts and SDKs | orchestration decisions |
| Memory | `kitt-memory` | durable semantic memory, provenance, retrieval, lifecycle and dream persistence | conversation execution state |
| Native Data Plane | `kitt-toolbox` | native repository/code intelligence and acceleration | model/provider policy |
| Resident Assistant | `kitt-assistant` | daemon, remote/control-center runtime and resident lifecycle | Agent task semantics |
| AI/Evaluation Workers | `kitt-ai-workers` | isolated eval/evolution/heavy AI workers | interactive Agent authority |
| Provider Gateway | `kitt-reverse-proxy` | authorized provider/API/web sessions and transport normalization | workspace mutation policy |

The root `rfdetoni/kitt` repository is the composition boundary: installers and `ecosystem.lock.json` freeze compatible revisions.

## Strategic rules

1. A context owns its data model and lifecycle.
2. Cross-context communication uses a public, versioned contract; shared implementation modules are not a shortcut.
3. The Agent is the authority for whether and when a tool executes against a workspace.
4. Memory owns durable semantic knowledge; the Agent owns conversation/history/evidence.
5. Reverse Proxy owns provider/browser transport; it does not decide repository permissions.
6. Toolbox owns acceleration; Python fallback preserves the same behavior contract.
7. UI is an adapter and cannot become a second application/domain authority.

## Agent layers

KITT already has domain concepts under `kitt/domain` such as `WorkspaceIdentity`, `SemanticTask`, constraints, context plans and edit results. New domain behavior should keep those concepts free from prompt-toolkit, provider SDK, SQLite and filesystem details.

A practical dependency direction is:

```text
presentation / CLI / TUI
          |
          v
application orchestration
          |
          v
domain concepts + ports
          ^
          |
infrastructure adapters
(SQLite, filesystem, providers, MCP, browser, native)
```

Python does not need a Java-style package hierarchy for every aggregate. Prefer explicit ports/protocols at real substitution boundaries over abstract base classes with one implementation.

## Aggregates and consistency

Use an aggregate only when KITT needs an explicit transactional consistency boundary. Examples that may justify aggregate-like treatment are a Goal with quality gates, an approval/pending-action lifecycle, or a Task Episode with evidence.

Do not treat every dataclass, provider response or database row as an aggregate.

## Repositories

A domain repository abstraction is appropriate only when domain/application code needs persistence without depending on the storage mechanism. Do not wrap an already-stable service solely to obtain the name "Repository".

## Domain services

Use a domain service when behavior represents domain policy that does not naturally belong to one entity/value object. Provider clients, SQLite helpers, renderers and HTTP gateways are infrastructure services, not domain services.

## Compatibility

Refactoring toward these boundaries must preserve:

- `kitt_runtime` model-facing operation names;
- protocol/schema versions unless intentionally migrated;
- installer/component ownership;
- Python fallback semantics when native acceleration is absent;
- existing CLI/TUI behavior unless the change is explicitly user-facing.

Cross-repository migrations require a compatibility window or an atomic ecosystem-lock promotion after all affected repositories pass their own gates.

## Architecture test principle

Architecture checks should enforce stable, high-value boundaries rather than cosmetic folder names. Existing module-boundary CI prevents Rust/native/daemon/evolution ownership from drifting back into Agent CLI. Add new executable rules only after the intended dependency direction is true in the codebase; never make CI bless an aspirational architecture by adding broad exceptions.
