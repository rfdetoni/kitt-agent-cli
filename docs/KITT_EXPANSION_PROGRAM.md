# K.I.T.T. Expansion Program

Status: capability-complete baseline, 2026-09-10.

This file is the capability roadmap, not a request to duplicate already-shipped implementations.

| Capability | State | Owner |
|---|---|---|
| Semantic task compiler | Implemented | Agent |
| Repository RAG/native search | Implemented | Agent + Toolbox |
| Incremental persistent index | Implemented | Agent |
| Tool-output compaction | Implemented | Agent |
| Lazy skills | Implemented | Agent |
| Lazy AGENTS/rules/specs, globs, DAG, priority, global budget | Implemented | Agent |
| Instruction considered-vs-injected telemetry | Implemented | Agent |
| Skill composition limits/handoffs | Implemented baseline | Agent |
| Capability broker/integrations | Implemented baseline | Agent |
| Retained child agents | Implemented | Agent |
| External child backend registry | Implemented | Agent |
| Worktree/security isolation for external workers | Implemented | Agent |
| SafeRuntime compact ACI | Implemented | Agent |
| ast-grep read-only structural search | Implemented | Agent |
| Semgrep changed-file security gate + hash cache | Implemented | Agent |
| LSP semantic facade: definition/hover/refs/diagnostics/call hierarchy/outline | Implemented optional adapter | Agent |
| Daemon/IPC/remote | Implemented | Assistant |
| Durable memory/Dreaming | Implemented | Agent + Memory |
| MCP/plugins/hooks | Implemented | Agent |
| Native symbol/index engine | Implemented | Toolbox + Agent bridge |
| Metrics/events | Implemented | Agent |
| OpenTelemetry | Implemented optional sink | Agent |
| Langfuse | Implemented via sanitized OTel sink | Agent |
| Adaptive routing with immutable security constraints | Implemented | Agent |
| Inspect-compatible eval bridge | Implemented | AI Workers |
| Promptfoo-compatible red-team corpus bridge | Implemented | AI Workers |
| `kitt doctor` ecosystem diagnostics | Implemented | Agent |
| Frozen-SHA cross-repo contract gate | Implemented | kitt composition |

## Operating rules

1. Security/privacy/capability constraints are hard constraints. Learning or telemetry must never weaken them.
2. External agents are explicit opt-in executors, never an implicit fallback.
3. Mutations produced by children remain inside KITT-owned worktrees and are integrated through KITT validation/path boundaries.
4. Optional integrations must degrade cleanly when absent.
5. Add abstractions only when they remove duplication or isolate a real boundary; keep KISS/DRY/YAGNI.
6. The composition repository validates exact SHAs so moving `main` branches cannot silently change a release contract.

Future changes should enter this roadmap only when backed by a concrete requirement, benchmark, failing contract, or security/evaluation finding.
