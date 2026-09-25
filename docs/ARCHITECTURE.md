# KITT Agent / Native Architecture

## Goal

Keep the Agent repository focused on orchestration while moving deterministic CPU/data-plane work to independently testable components without changing the model-facing API.

```text
User
 │
 ▼
kitt-agent-cli (Python control plane)
 │
 ├─ routing / providers / policy / approvals / goals / children
 ├─ plugins / MCP / hooks / integrations
 ├─ Dreaming / history / state / telemetry
 └─ kitt_runtime
        │
        ├─ kitt/native/* adapter + safe Python fallback
        │        │
        │        └─ optional kitt_native wheel
        │                 owned by kitt-toolbox
        │
        └─ assistant/evolution capabilities are composed as sibling packages
```

## Ownership boundary

`rfdetoni/kitt-toolbox` owns the Rust crates `kitt-native-engine` and `kitt-native-python`, their Maturin build, Rust formatting/lint/tests and the `kitt_native` wheel. `kitt-agent-cli` does **not** vendor or compile those crates.

The Agent keeps `kitt/native/bridge.py` and the portable fallback because runtime selection, policy integration and graceful degradation are control-plane responsibilities. If `kitt_native` is installed, the bridge selects the Rust backend; otherwise the same interfaces use the Python fallback.

`rfdetoni/kitt-assistant` owns the persistent Assistant service and the companion Python daemon/remote runtime. `rfdetoni/kitt-ai-workers` owns separately packaged Evolution/Evals plus optional heavier AI workers. `rfdetoni/kitt-memory` owns the reusable persistent memory data plane: bounded baselines, FTS/hybrid-ready retrieval, durable corrections and reusable knowledge. The official installers compose these packages into one `kitt` namespace.

`kitt/extensions` and `kitt/integrations` intentionally remain in the Agent. Plugins, MCP, hooks and external-tool selection execute inside the Agent policy/security boundary and therefore belong to the coding control plane rather than to AI workers or the native toolbox.


## TUI ownership and performance boundary

The full-screen TUI is a thin presentation layer around the existing runtime contracts. `KittRuntime`, `TurnEventBridge`, repository/context services and command handlers remain authoritative for execution and data. UI modules are separated by responsibility: retained controls, keybindings, scroll/mouse routing, rendering, command dispatch, model/provider flows and runtime actions. See [TUI_ARCHITECTURE.md](TUI_ARCHITECTURE.md).

The UI keeps `Window`, `Control` and `Buffer` instances stable after construction. `invalidate()` does not rebuild the container tree. Rendering also rebuilds only the small interaction hit map for the visible surface: semantic row/button regions are derived from the rendered cell layout, while controller actions remain outside rendering. Mouse wheel routing is local to a registered surface, which prevents cross-panel state mutations and avoids global scroll dispatch on the hot render path.

## Native code intelligence

The toolbox native engine is provider/UI/session agnostic. Its public domain is repository/file/symbol/query/reference/edit/output. It provides gitignore-aware walking, token-budgeted search, Tree-sitter symbol intelligence, optimistic structural edits with source hashes, syntax validation and deterministic process-output reduction.

The model still sees the compact policy-governed `kitt_runtime` surface. Native acceleration is an implementation detail underneath operations such as `repo.search`, `repo.inspect_symbol`, `repo.read_symbol`, `repo.references`, `repo.edit_symbol` and `process.run`.

## Memory and multi-agent state

Dreaming, history, goals, approvals, artifacts and workspace execution state remain Agent-owned. Reusable durable memory primitives belong in `kitt-memory`; the Agent remains responsible for deciding when session evidence becomes durable knowledge and for orchestrating Dreaming/consolidation.

Child worktrees isolate code edits while the original workspace remains the state root for history, approvals, memory and coordination. Isolation is reinforced by KITT's own mutation coordination layer:

- path and symbol claims are acquired atomically, including bounded dependency reads for structural edits;
- path claims understand ancestor/descendant overlap, so a directory writer conflicts with writers below it;
- contention is represented in a durable ordered wait queue rather than by unbounded model retries;
- active retained children renew ownership leases across execution and approval waits;
- mutation fencing occurs after authority/approval checks but immediately before the side effect;
- terminal child history remains durable without permanently consuming execution capacity.

These mechanisms extend the existing runtime and EventBus/state model; KITT does not introduce a second agent/workflow framework for coordination.

## Durable event, evidence and projection plane

The Agent now has one durable event ledger for execution evidence. The provider
request is recorded immediately before dispatch, so model-visible state can be
reconstructed from durable facts instead of from ad-hoc in-memory assembly.
Ordinary turn/tool events are stored as bounded evidence records while exact
provider requests retain the model-visible payload needed for replay.

`SessionProjectionRegistry` folds the ledger with pure reducers. Projection state
is hot in memory and checkpointed sparsely in SQLite; cold reads seed from the
last checkpoint and replay only the tail. Current built-in projections cover turn
state, tool activity, Task Episode state and model-request activity.

Task Episodes sit above turns. A non-goal turn gets a single-turn Episode; turns
belonging to the same active Goal reuse that Goal-backed Episode. Deliverables and
validation evidence are attached to the Episode so evaluation is based on the
user objective rather than aggregate conversation length.

Runtime invariants are observe-first and package-owned. The default mode records
violations without changing execution. `KITT_RUNTIME_INVARIANTS=STRICT` promotes
critical invariant failures to fail-fast behavior for validation environments.

## Execution compression

`flow.execute` remains the bounded dependency-DAG executor for deterministic
read-only fan-out. `program.execute` is a second compression form for small
loops and branches. It interprets a fixed data/control IR and delegates every
host call back through SafeRuntime with the original security context and
capabilities; it cannot execute arbitrary Python, shell or external tools.

## Child providers and extension lifecycle

External child CLIs are now adapters behind a capability-oriented Child Provider
protocol. Existing Codex/Claude/OpenCode/Aider/Gemini/OpenHands/Prime behavior
remains available through `ExternalCliChildProvider`, while continuation,
structured output, model/reasoning overrides, filters and interrupt support are
declared capabilities rather than backend-name conditionals.

Dynamic extension registrations have an `EffectScope`. Tool/hook ownership is
disposed in reverse registration order on plugin unload, reducing leaked
callbacks and stale authority after reloads.

## Harness evidence and controlled evolution

Harness configuration can be frozen as a content-addressed snapshot. Runtime
operations and other resolved components emit materialization receipts that
separate requested, resolved and actually materialized capability facts.

The intervention ledger stores baseline metrics, candidate causes, owner,
validation route, guardrail metric, comparison window and stop/revert condition.
Later comparable evidence determines `IMPROVING`, `UNCHANGED`,
`REGRESSING` or `OUTCOME_SUPPORTED`; same-window validation alone does not
prove longitudinal effectiveness.

The local control plane also owns revisioned presets, component drift snapshots,
Episode efficiency, golden replay and bounded baseline/candidate experiments.
Experiment arms reuse `WorkspaceCoordinator` Git worktrees and fail closed when
isolation is unavailable. Heavy batch/model-scale evaluation remains suitable
for `kitt-ai-workers`; both paths share the same evidence and snapshot
contracts. See [HARNESS_EVOLUTION.md](HARNESS_EVOLUTION.md).

## Packaging

`kitt-agent-cli` is a universal Python control-plane distribution. Its release CI does not build Rust. Native wheels are built and validated in `kitt-toolbox`; the Assistant Python runtime is packaged from `kitt-assistant`; Evolution/Evals are packaged from `kitt-ai-workers`.

The standalone and ecosystem installers install the Agent first, then compose companion packages. Native acceleration is optional and fails closed to the Python fallback. This keeps source installation usable without Rust while preserving the faster backend when the toolbox wheel is available.

## Development gates

Agent changes are validated with Python compilation/tests and the clean-room provenance guard. Rust formatting, Clippy and workspace tests belong to `kitt-toolbox`. Cross-repository compatibility is validated by the frozen-SHA workflow in `rfdetoni/kitt`.

## Context lifecycle and provider-cache boundary

The provider-facing prompt is split into an invariant prefix and a dynamic turn
tail. The invariant prefix contains execution behavior plus the stable
model-facing host-tool contract. Query-specific memory, skills, formatting
rules, harness guidance, retrieved files, repository maps and conversation
history follow it. Host authorization remains authoritative; moving the
per-turn operation allowlist later in the prompt does not broaden runtime
capabilities.

For stateless/local providers, host results that have already been consumed by
the model are eligible for receipt compaction. A receipt retains the tool name,
content digest, original token estimate, optional artifact identifier and a
bounded excerpt. Browser-backed reverse-proxy histories are not rewritten,
because their message sequence participates in browser conversation identity.

History compaction is pressure-based: KITT compares active-history tokens with
the selected model's usable input budget and compacts only after the configured
ratio is reached. This avoids treating a dozen tiny messages the same as a
dozen very large messages.

`flow.execute` remains read-only and policy bounded. Its steps are interpreted
as a dependency graph: independent nodes may run concurrently, while data
references and explicit `depends_on` edges create deterministic barriers.



### Reverse Proxy control plane

The Agent treats KITT Reverse Proxy as an external control-plane boundary. `kitt/reverse_proxy/client.py` executes only argv-based machine-readable commands and maps schema-v1 responses into immutable contracts. TUI code never discovers PIDs, allocates ports or loads provider plugins itself.

Role assignment remains owned by the existing task router. Binding an instance calls the same model-role service used by F12, so Context, Principal/Code and Validation can point to independent reverse-proxy endpoints without a parallel routing system.


## TUI interaction boundary

Agent CLI 0.72 introduces `kitt/ui/interaction.py` as the single owner of local-cell hit testing. The design borrows the interaction concepts that are useful from OpenTUI—rendered-cell hit targets, focus ownership and keyboard/mouse parity—without adopting its renderer or runtime. Render functions register semantic regions; `kitt/ui/mouse.py` translates pointer events to those semantic actions; existing controllers perform the mutations. This preserves SOLID ownership and keeps input mechanics independent from reverse-proxy, router and approval business rules.
