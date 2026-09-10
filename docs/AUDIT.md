# K.I.T.T. Agent Audit

Last updated: 2026-09-10.

## Current ownership

`kitt-agent-cli` is the Python control plane. It owns routing/providers, policy and approvals, goals and retained children, plugins/MCP/hooks, history/memory orchestration, telemetry, `kitt_runtime`, and the Python native bridge/fallback.

It does **not** own Rust crates or native wheel builds (`kitt-toolbox`), the daemon/remote runtime (`kitt-assistant`), or eval/evolution packages (`kitt-ai-workers`). The module-boundary workflow is authoritative and must reject those ownership regressions.

## Implemented hardening and performance

- Incremental repository index with no-op rebuild avoidance and O(1) repository graph edge updates.
- Workspace/path trust boundary, capability-scoped `SafeRuntime`, approvals, mutation preconditions and post-edit syntax gates.
- Lazy AGENTS/rules/specs selection with deterministic priority, globs, dependency ordering, cycle cutting, a global body budget, and considered-vs-injected telemetry.
- Native-first semantic facade with optional LSP, read-only ast-grep, and changed-file-only Semgrep with content-hash cache.
- Retained-agent worktree isolation plus explicit opt-in external backends (Codex, Claude Code, OpenCode, Aider, Gemini CLI, OpenHands and Prime Agent). External executors run with `shell=False`, sanitized environment, bounded output, cancellation and KITT worktree integration.
- OpenTelemetry plus optional Langfuse-compatible OTel sink; public telemetry remains bounded and secret/reasoning sanitized.
- Adaptive routing keeps privacy/capability hard constraints authoritative; telemetry can influence quality/cost preference but cannot relax policy.
- `kitt doctor` reports lifecycle states: `AVAILABLE`, `CONFIGURED`, `AUTHENTICATED`, `DEGRADED`, `UNAVAILABLE`, while retaining legacy PASS/WARN/INFO output.

## Validation gates

Agent-local validation:

```bash
python -m compileall -q kitt tests
python -m pytest -q
python packaging/verify_cleanroom.py
```

Native Rust validation belongs to `kitt-toolbox`; daemon/remote validation belongs to `kitt-assistant`; eval/evolution validation belongs to `kitt-ai-workers`. Cross-repository validation belongs to the `kitt` composition repository and must use immutable commit SHAs.

## Remaining work policy

There are no architectural roadmap items that require reintroducing migrated ownership into Agent. Further work should be driven by failing tests, measured performance regressions, security findings, or new product requirements rather than speculative framework expansion.
