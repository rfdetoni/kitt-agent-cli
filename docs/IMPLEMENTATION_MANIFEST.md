# K.I.T.T. Implementation Manifest

Last updated: 2026-09-25.

## `rfdetoni/kitt-agent-cli`

Owns the Python control plane:

- `kitt/core`, router, providers and runtime composition
- security policy, capabilities, approvals and mutation preconditions
- context engine/index orchestration and lazy instruction selection
- retained children, external child backend strategy and worktree integration
- tools, `kitt_runtime`, plugins/MCP/hooks/integrations
- observability, metrics, history, memory orchestration and Dreaming integration
- Python `kitt/native` bridge and compatibility fallback
- retained full-screen TUI presentation layer (`kitt/ui`), including scroll routing, overlays, keyboard discovery, rendering and provider/model setup UI

The Agent repository intentionally does not contain Rust crates/Cargo workspace files, `kitt/daemon`, `kitt/remote`, `kitt/evals`, or `kitt/evolution`.

## `rfdetoni/kitt-toolbox`

Owns the Rust native implementation and Python wheel:

- `kitt-native-engine`
- `kitt-native-python`
- Cargo/Maturin build and `kitt_native` release artifacts

Agent must remain fully functional through its Python compatibility backend when the native wheel is unavailable.

## `rfdetoni/kitt-assistant`

Owns persistent/resident Assistant capabilities, including the companion Python runtime package:

- `packages/kitt-assistant-runtime/kitt/daemon`
- `packages/kitt-assistant-runtime/kitt/remote`
- resident service/UI/platform packaging

## `rfdetoni/kitt-ai-workers`

Owns optional heavier AI workers:

- `packages/kitt-evals/kitt/evals`
- `packages/kitt-evolution/kitt/evolution`
- worker services

## Composition

`rfdetoni/kitt` is the integration owner. It pins immutable ecosystem SHAs, installs the split packages together, verifies ownership boundaries, and checks native-present/native-absent fallback contracts.

No source-copy migration scripts are part of the normal architecture. Changes are made in the owning repository and integrated through package contracts.


## Agent CLI 0.70.0 TUI boundary

Agent CLI 0.70.0 refactors the retained TUI behind existing runtime/protocol contracts:

- `KittUIApp` is a lifecycle/orchestration facade; controls, rendering, navigation, provider/model flows, scroll/mouse routing and runtime actions are separate owners.
- Mouse wheel routing is panel-local and application mouse support is enabled by default; `F10`/`/mouse` restores native terminal selection.
- The layout is capped at five physical `Float` surfaces and reuses shared model/provider and contextual panels.
- Idle/home animation no longer continuously invalidates the terminal, the UI blocking executor is bounded to two workers, and TUI construction does not probe the reverse proxy network endpoint.
- No daemon, protocol, native, memory, toolbox, reverse-proxy or assistant-runtime interface changed. Sibling KITT repositories therefore require no compatibility version bump for this release.
