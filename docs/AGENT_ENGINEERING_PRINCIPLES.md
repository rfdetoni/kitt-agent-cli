# KITT Agent Engineering Principles

KITT is a product/runtime, not an integration shell for agent frameworks. External
projects and papers are inputs for principles, benchmarks and failure modes; they
are not architectural dependencies by default.

## Product objective

Every runtime change must improve at least one of these dimensions without an
unbounded regression in another:

1. **Quality** — probability of solving the user's actual task correctly.
2. **Efficiency** — tokens, model round-trips, latency and compute per solved task.
3. **Reliability** — probability of completing without loops, corrupted state or
   unrecoverable failure.
4. **Autonomy** — task duration and complexity KITT can finish safely without
   unnecessary user intervention.

A capability that cannot state its expected effect on one of these dimensions is
not ready for the core runtime.

## Non-negotiable runtime principles

### Context precision over context size

Repository, memory, history and tool output are candidate context, not automatic
prompt content. KITT should progressively discover, rank, deduplicate, bound and
compress context before spending executor-model tokens.

Prefer structure before payload: repository maps and symbols before whole files,
relevant ranges before whole files, compact handles before repeated large output.

### Deterministic where possible, model-driven where valuable

LLMs decide when ambiguity or semantic reasoning is valuable. Parsing, schema
validation, path enforcement, hashing, retry bounds, exact retrieval, diff
application and quality checks stay deterministic.

Do not spend a model call on work that an existing bounded deterministic primitive
can perform with equal or greater correctness.

### Agent Computer Interface is a first-class API

Tools are designed for models, not merely wrapped human APIs. A model-facing tool
must have bounded output, explicit failure semantics and the smallest sufficient
contract.

The runtime operation catalog is generated from the executable runtime contract.
Do not maintain a second authoritative list in prompts or documentation.

### Progressive disclosure

Default retrieval returns the least information required for the next decision.
The model explicitly drills down when detail is needed. Large intermediate payloads
should remain outside model context whenever a compact handle or summary is enough.

### Execution compression

Optimize **model round-trips per solved task**, not only input tokens. When several
safe read-only operations form a deterministic dataflow, `flow.execute` may compose
them and expose only the bounded final result plus execution metadata.

Execution compression must remain fail-closed: no mutation, arbitrary process,
external MCP/skill invocation, approval-requiring action or expensive security scan
may be smuggled through a read-only flow.

### Verification before confidence

KITT does not accept its own prose as proof of success. Prefer verifiable evidence:
post-edit syntax validation, compiler/type checker, tests, lint/static analysis,
security checks and explicit goal quality gates.

Passing one validator is evidence, not proof of overall correctness. Validation
should match the requested behavior and avoid unrelated changes.

### Memory is knowledge, not transcript

Conversation history is source material. Persistent memory should represent useful
facts, decisions, procedures, relationships and episodes with provenance and
bounded retrieval.

Code-derived knowledge is temporal. It must be possible to invalidate, supersede
or re-verify it when the repository changes.

### Failure is explicit state

Timeout, retryable failure, approval wait, child wait, cancellation, validation
failure and terminal failure are explicit runtime conditions. Long-running work
must not depend on reconstructing hidden progress from prose.

New durable-execution work should reuse KITT's existing turn/goal/runtime state and
SQLite event/state facilities rather than introduce a parallel workflow framework.

### Security grows with autonomy

More autonomous execution requires stronger capability scoping, path policy,
approval boundaries, credential isolation, cancellation barriers, leases/fencing
and bounded subprocess/network behavior.

No efficiency optimization is allowed to bypass an existing security or approval
boundary.

### Provider independence

Routing may exploit provider/model capabilities, but no core semantic, memory,
retrieval, execution or verification capability may require one model vendor.
Provider-specific optimizations belong behind capability adapters.

### Protocols live at the edges

MCP, A2A and future interoperability protocols are adapters around KITT's internal
contracts. External protocol object models must not become the core domain model.

### Observability is part of correctness

Important decisions must be measurable: selected context, model/provider, token
budget, cache behavior, tool calls, hidden intermediate tokens, retries, approvals,
validation results, latency and terminal outcome.

Avoid duplicate telemetry pipelines. Extend existing event/metrics/OTel surfaces.

### Every real failure can become an eval

Production bugs, malformed tool calls, retrieval misses, unsafe attempts and
regressions are candidates for deterministic or trajectory regression tests. KITT
should improve from failures through tests and memory/rules, not by accumulating
prompt folklore.

## Core admission checklist

Before adding a new agent capability, answer all of the following:

- Which of Quality, Efficiency, Reliability or Autonomy should improve?
- What existing KITT primitive can be extended instead of adding a subsystem?
- Can the behavior be deterministic or bounded before involving an LLM?
- What is the model-facing token and round-trip cost?
- What is the security/approval boundary?
- What happens on timeout, cancellation, crash or partial failure?
- What telemetry proves whether the change helped?
- What regression test prevents the capability from silently degrading?
- Does this introduce provider, framework or infrastructure lock-in?

If the answers are unclear, keep the capability outside the core until evidence
justifies it.
