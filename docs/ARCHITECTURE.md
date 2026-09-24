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

