# K.I.T.T. Expansion Program

This document is the canonical capability plan for the K.I.T.T. agent platform.
It consolidates previously discussed work (lazy skills, executable skills,
retained agents, daemon, MCP/plugins/hooks, dreaming, provider auth, RTK-style
context reduction) with the newer semantic-code, security, observability,
evaluation and external-agent integrations.

## Architectural rule

K.I.T.T. remains the control plane. External tools are optional workers/backends,
not trusted orchestration authorities. Policy, capabilities, path scope, worktree
isolation, approval, token budgets, telemetry and verification remain K.I.T.T.-owned.

Optional integrations MUST:

1. be detected without installation or network access;
2. never become mandatory runtime dependencies;
3. run without a shell unless an existing policy-governed process surface explicitly permits it;
4. preserve workspace/path scope;
5. keep mutation behind K.I.T.T. approval + precondition + diff validation;
6. emit bounded/redacted telemetry;
7. degrade cleanly when absent.

## Capability matrix

| Area | Capability | State | Target / invariant |
|---|---|---|---|
| Context | Semantic task compiler | Implemented | Deterministic IR + confidence + constraints |
| Context | Repository RAG / native search | Implemented | Bounded retrieval, working-set aware |
| Context | Lazy skills | Enhanced | Active+trusted only, semantic routing, globs, keywords, dependency DAG, one global body budget |
| Context | Lazy rules/specs | Expand | `alwaysApply`, globs, dependency DAG, deterministic priority, global instruction budget |
| Context | Tool-output compaction | Implemented | Artifact offload + RTK-style gain accounting |
| Context | Session search | Implemented | Workspace-scoped FTS, compact snippets, token budget |
| Skills | Executable skills | Implemented | Explicit trust, inherited capabilities, subprocess sandbox |
| Skills | Skill composition | Expand | Depth/cycle/budget limits and structured handoff |
| Agents | Retained child agents | Implemented | Capability/path scope, token/time budgets, worktree coordination |
| Agents | External child backend | Adapter planned | Codex, Claude Code, OpenHands, OpenCode, Aider, Gemini CLI, Prime Agent |
| Runtime | SafeRuntime compact ACI | Implemented | Policy-governed model-facing surface |
| Runtime | Persistent daemon / IPC | Implemented | Single scheduler/extensions owner |
| Runtime | Capability broker | Implemented/expand | Explicit principal capabilities; extend to integrations |
| Runtime | Scheduler | Implemented | Durable scheduled execution, daemon owned |
| Memory | Durable memory | Implemented | Workspace-aware retrieval and corrections |
| Memory | Dreaming / consolidation | Implemented | Bounded gather/consolidate/prune cycles |
| Extensions | MCP | Implemented | Trust/policy boundary and hardened transport |
| Extensions | Plugins | Implemented | Owner lifecycle, capability restrictions |
| Extensions | Hooks | Implemented | Controlled lifecycle hooks |
| Providers | Native API providers | Implemented | Router, credentials, local/cloud privacy classes |
| Providers | Reverse-proxy browser providers | Implemented | Dynamic capabilities, session identity, LRU/TTL |
| Providers | LiteLLM gateway | Optional adapter | API-provider routing only; never replaces browser reverse proxy |
| Semantic code | Native symbol/index engine | Implemented | Rust fast path + Python compatibility path |
| Semantic code | ast-grep | Adapter added | Read-only AST search now; rewrites must become KITT patches |
| Semantic code | Serena / LSP | Optional adapter | Definitions, hover, references, diagnostics, call hierarchy |
| Security | Existing capability/path/network guards | Implemented | Fail closed |
| Security | Semgrep | Adapter added | Local-config scan, no implicit remote `auto` rules |
| Security | Coding-agent red team | Planned bridge | Promptfoo core coding-agent probes in CI |
| Evaluation | Native benchmarks/evals | Implemented | Retrieval, context, native engine, scale |
| Evaluation | Inspect AI bridge | Planned bridge | Run KITT and peer agents under same evaluation harness |
| Observability | KITT metrics/events | Implemented | Turn/tool/gain/native telemetry |
| Observability | OpenTelemetry | Planned optional sink | goal -> turn -> agent -> model/retrieval/tool/edit/validation spans |
| Observability | Langfuse | Planned optional sink | OTEL/evaluation consumer, never core dependency |
| UX | TUI / daemon integration | Implemented | Non-blocking UI and control center |
| UX | `/gain` | Implemented | RTK-style estimated token-savings analytics |
| UX | Integration diagnostics | In progress | `kitt doctor` reports optional capability availability |

## Lazy instruction contract

Skill frontmatter may use:

```yaml
---
name: java-performance
description: Java/Spring performance review workflow
alwaysApply: false
priority: 40
globs: ["src/**/*.java", "pom.xml"]
keywords: ["spring", "hibernate", "jpa", "performance"]
depends_on: ["java-core"]
---
```

Selection rules:

- inactive skills are excluded;
- untrusted workspace skills are excluded;
- explicit `/skill` or `@skill` references receive highest task relevance;
- `alwaysApply` skills are retained subject to the hard global budget;
- matching paths/globs and keywords increase relevance;
- dependencies are loaded before dependents;
- cycles are cut deterministically;
- selected bodies are section-ranked and excerpted rather than blindly injected;
- one global body budget is shared across selected skills.

The same model should be extended to AGENTS/rules/spec instruction sources so all
instruction families compete inside one global prompt budget rather than each
having an independent unbounded allowance.

## Optional integration catalog

The zero-dependency catalog detects these families without installing or invoking
anything by default:

- Semantic/code: ast-grep, Serena, Pyright, TypeScript Language Server, rust-analyzer, gopls, jdtls
- Security: Semgrep
- External agents: Codex, Claude Code, OpenHands, OpenCode, Aider, Gemini CLI, Prime Agent
- Gateway: LiteLLM
- Evaluation: Inspect AI, Promptfoo
- Observability: OpenTelemetry, Langfuse

`ast-grep` and `Semgrep` have KITT-owned adapters. Their subprocesses are exact
argv invocations (no shell), bounded in time/output and workspace-scoped.
Direct external rewrites are intentionally not enabled.

## External child-agent backend contract

Future external child execution must use a `ChildAgentBackend` contract with:

- backend id and availability probe;
- one isolated KITT-prepared worktree;
- inherited/reduced `ExecutionSecurityContext`;
- allowed paths/capabilities/tools;
- token/cost/deadline budget;
- bounded stdout/stderr transcript;
- cancellation and process-tree kill;
- structured result + changed-files manifest;
- KITT-owned validation gates before integration;
- no direct merge/push from the worker.

Workers to support: `kitt-native`, `codex`, `claude-code`, `openhands`, `opencode`,
`aider`, `gemini-cli`, `prime-agent`, and a generic MCP/command backend.

## Semantic backend contract

Expose a common semantic interface regardless of source:

- `definition(symbol/location)`
- `hover(location)`
- `references(symbol/location)`
- `diagnostics(paths)`
- `call_hierarchy(symbol/location)`
- `outline(paths)`
- `ast_search(pattern, language, paths)`

Preferred order: KITT native index for cheap structural facts, language server for
compiler/IDE semantics, ast-grep for structural patterns. Serena may be consumed
through the existing MCP surface when installed.

## Security gates

Recommended post-edit pipeline:

1. native changed-file validation;
2. project tests/build gates;
3. Semgrep changed-file scan when a local config is available;
4. dependency/security checks already configured by the repository;
5. Promptfoo coding-agent red-team suite in dedicated CI/nightly runs;
6. protected-hash/canary verification for agent harness evaluation.

Security scanners are evidence providers. They never receive authority to mutate
KITT state automatically.

## Evaluation program

Track at least:

- task completion rate;
- first-pass test success;
- regression rate;
- tool calls / turn;
- raw vs returned tool tokens (`/gain`);
- total model input/output tokens;
- context retrieval precision/recall on labeled corpora;
- wall time and p50/p95/p99;
- child-agent success by backend/task class;
- approval frequency;
- unsafe-action blocks;
- red-team pass rate.

Inspect AI should compare KITT-native and external child backends under the same
tasks. Promptfoo should exercise repository prompt injection, terminal-output
injection, secret access, network exfiltration, verifier tampering and sandbox
boundary failures.

## Delivery order

### P0 — Context and trust
- Lazy skill v2 (active/trusted semantic routing, DAG, global budget) — implemented in this program.
- Apply the same selector/budget model to rules/specs/AGENTS fragments.
- Add gain telemetry for instruction bytes considered vs injected.

### P1 — Semantic and security
- Register ast-grep read-only search through SafeRuntime.
- Register Semgrep local-config scans as a quality/security gate.
- Add semantic backend interface and LSP/Serena bridge.
- Add changed-file-only scanning and cache results by content hash.

### P2 — External agent control plane
- Introduce `ChildAgentBackend` registry.
- Route external workers only through KITT worktrees and reduced security contexts.
- Add backend routing metrics and fallback to kitt-native.

### P3 — Observability and evaluation
- Add optional OpenTelemetry event/span sink.
- Export Inspect-compatible eval tasks/results.
- Add Promptfoo coding-agent red-team CI profile.
- Add optional Langfuse sink via OTEL.

### P4 — Adaptive routing
- Learn task-class/backend quality from empirical eval/production telemetry.
- Prefer the cheapest backend meeting confidence/quality thresholds.
- Never learn a policy relaxation from success telemetry.

## Non-goals

- Replacing KITT with another agent framework.
- Giving external agents unrestricted repository or GitHub access.
- Installing optional tools automatically during normal execution.
- Sending repository content to external services merely to discover capabilities.
- Summing `/gain` tool-output savings with context-pipeline savings (double-count risk).
