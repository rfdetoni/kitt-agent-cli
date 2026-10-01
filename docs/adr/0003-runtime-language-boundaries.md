# ADR 0003 — Runtime language boundaries

Status: Accepted for Agent CLI 0.81.0 completion.

K.I.T.T. chooses language per ownership boundary. Python remains the orchestration/control-plane language where provider, policy, TUI and workflow composition dominate. Rust owns Memory storage/jobs and optional deterministic/native hot paths where concurrency, bounded memory use and data-plane throughput dominate.

| Component | Chosen language | Reason | Expected bottleneck | Why the other language was rejected | Interop boundary | Validation/benchmark |
|---|---|---|---|---|---|---|
| EventLedger | Python | Coupled to Agent HistoryDatabase and lifecycle | SQLite durability | Rust adds a boundary without moving storage cost | durable event schema / IDs | Not performance-motivated; validated by integration/latency regression gates. |
| RunCoordinator | Python | Owns Agent cancellation/run lifecycle | provider/tool latency | Rust complicates cancellation ownership | turn IDs / durable events | Not performance-motivated; validated by integration/latency regression gates. |
| ResourceCoordinator | Python | Agent owns resource scheduling | external contention | cross-language locks obscure ownership | canonical resource IDs | Not performance-motivated; validated by integration/latency regression gates. |
| ContextCompiler/ContextEpoch | Python | Composes Agent sources into Protocol contracts | repository/provider I/O | Rust would duplicate orchestration policy | ContextEnvelope/ContextSegment | Not performance-motivated; validated by integration/latency regression gates. |
| Progressive Memory retrieval | Rust | Memory owns retrieval/hydration/provenance | search/storage | Python Agent code would create competing authority | memory.search/timeline/get | Memory progressive integration + latency gates |
| Memory jobs/storage | Rust | memoryd owns durable lease/retry/idempotency | durable storage/indexing | Python persistence violates single authority | authenticated Protocol frames | crash/retry/lease/idempotency integration |
| ExecutionBudget | Python | Turn-scoped orchestration wallet | negligible bookkeeping | Rust adds synchronization/interop | ExecutionBudget/BudgetLease | Not performance-motivated; validated by integration/latency regression gates. |
| ExecPolicy/authority orchestration | Python | Coupled to capabilities/approvals | OS/runtime operations | moving policy splits authority | capabilities / runtime ops | Not performance-motivated; validated by integration/latency regression gates. |
| ConversationRuntime control plane | Python | Binds conversation to runtime adapters | runtime CLI/API latency | Rust cannot reduce container startup cost | ConversationRuntimeBinding | Not performance-motivated; validated by integration/latency regression gates. |
| Process/runtime mediation | Python | Authority + durable lifecycle stay in Agent | child process execution | Rust would duplicate process authority | argv-only runtime ops | Not performance-motivated; validated by integration/latency regression gates. |
| Artifact recovery | Python | Agent owns context-lifecycle references | local I/O | no measured CPU bottleneck | artifact ID/query/offset/bounds | Not performance-motivated; validated by integration/latency regression gates. |
| Workspace snapshots | Python | Coupled to Agent mutation/rollback policy | filesystem I/O | Rust splits mutation ownership | canonical paths / receipts | Not performance-motivated; validated by integration/latency regression gates. |
| Subagents | Python | Parent/child lineage and leases are orchestration | provider/runtime work | Rust adds lifecycle complexity | BudgetLease / child events | Not performance-motivated; validated by integration/latency regression gates. |
| Stuck/no-progress | Python | Consumes Agent execution/progress events | bounded comparisons | no performance case | normalized progress signatures | Not performance-motivated; validated by integration/latency regression gates. |
| kitt learn/experiments | Python | Evidence analytics must not become policy authority | SQLite aggregation | Rust adds boundary before evidence of need | sanitized metrics / experiment state | Not performance-motivated; validated by integration/latency regression gates. |

## Type-check dependency decision

Mypy is installed only by CI for release validation of changed agentic boundaries. It is not a production dependency. This adds real type checking in addition to `compileall` without increasing runtime installation cost.
