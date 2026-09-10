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

`rfdetoni/kitt-assistant` owns the persistent Assistant service and the companion Python daemon/remote runtime. `rfdetoni/kitt-ai-workers` owns separately packaged Evolution/Evals plus optional heavier AI workers. The official installers compose these packages into one `kitt` namespace.

`kitt/extensions` and `kitt/integrations` intentionally remain in the Agent. Plugins, MCP, hooks and external-tool selection execute inside the Agent policy/security boundary and therefore belong to the coding control plane rather than to AI workers or the native toolbox.

## Native code intelligence

The toolbox native engine is provider/UI/session agnostic. Its public domain is repository/file/symbol/query/reference/edit/output. It provides gitignore-aware walking, token-budgeted search, Tree-sitter symbol intelligence, optimistic structural edits with source hashes, syntax validation and deterministic process-output reduction.

The model still sees the compact policy-governed `kitt_runtime` surface. Native acceleration is an implementation detail underneath operations such as `repo.search`, `repo.inspect_symbol`, `repo.read_symbol`, `repo.references`, `repo.edit_symbol` and `process.run`.

## Memory and multi-agent state

Dreaming, history, goals, approvals, artifacts and workspace state remain Agent-owned. Native acceleration augments these systems but does not create a second authority. Child worktrees isolate code edits while the original workspace remains the state root for history, approvals, memory and coordination.

## Packaging

`kitt-agent-cli` is a universal Python control-plane distribution. Its release CI does not build Rust. Native wheels are built and validated in `kitt-toolbox`; the Assistant Python runtime is packaged from `kitt-assistant`; Evolution/Evals are packaged from `kitt-ai-workers`.

The standalone and ecosystem installers install the Agent first, then compose companion packages. Native acceleration is optional and fails closed to the Python fallback. This keeps source installation usable without Rust while preserving the faster backend when the toolbox wheel is available.

## Development gates

Agent changes are validated with Python compilation/tests and the clean-room provenance guard. Rust formatting, Clippy and workspace tests belong to `kitt-toolbox`. Cross-repository compatibility is validated by the frozen-SHA workflow in `rfdetoni/kitt`.
