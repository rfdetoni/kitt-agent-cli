# KITT Native Subsystem Architecture

## Goal

Make KITT substantially more capable with small/local models by moving deterministic repository navigation, output reduction, memory retrieval and multi-agent collision prevention out of the LLM loop.

```text
User
 │
 ▼
KITT Agent / Task Router (Python)
 │
 ├─ model reasoning
 │
 └─ kitt_runtime ───────────────────────────────────────┐
                                                        │
                                                        ▼
                                             KITT Native Subsystem
                                                        │
       ┌──────────────────────┬─────────────────────────┼────────────────────┐
       ▼                      ▼                         ▼                    ▼
 Code Intelligence      Output Optimizer          Memory Engine      Coordinator
       │                      │                         │                    │
 Rust native              deterministic          existing Dreaming      worktrees
 + Python fallback        family filters         + hybrid recall         leases
       │                      │                   + knowledge graph       merge gate
       ▼                      ▼                   + corrections            │
 unified symbols        raw retained as                 │                  ▼
 / refs / deps          KITT artifact                   └──── SQLite ───────┘
```

## Native code intelligence

`kitt-native-engine` is pure Rust and knows nothing about LLM providers, prompts, sessions or UI. Its public domain is repository/file/symbol/query/reference/edit/output.

Capabilities:

- gitignore-aware repository walking;
- token-budgeted text/regex search;
- grouping limits globally and per file;
- match-aware long-line slicing;
- Tree-sitter symbol extraction for Python, Java, JavaScript, TypeScript/TSX, Rust and Go;
- symbol reads by stable `path::qualified_name` identity;
- reference lookup and dependency-edge extraction;
- optimistic symbol replacement using source hash;
- syntax validation before atomic persistence;
- deterministic process-output optimization;
- `never worse`: compact output is used only when smaller and non-empty.

`kitt-native-python` contains only PyO3 bindings. `kitt/native/bridge.py` owns runtime selection and uses `fallback.py` if the extension is unavailable.

## Model-facing API

No new top-level tools are required. KITT keeps the single `kitt_runtime` surface for small/local models. Added/accelerated operations are:

- `repo.search`
- `repo.inspect_symbol`
- `repo.read_symbol`
- `repo.references`
- `repo.edit_symbol`
- `memory.query`
- `memory.correct`
- `memory.concept`
- `memory.link`
- `process.run` (same operation, optimized result)

This avoids sending many extra tool schemas to the model.

## Output optimization

Output reduction is command-family aware instead of blind truncation:

- search: group matches by file, cap matches per file;
- build/test: retain failures, assertions, exceptions, warnings and summaries plus nearby context;
- infrastructure: emphasize errors/status summaries;
- generic: bounded head/tail only for very large output;
- git: preserve normal diffs/status unless output becomes abnormally large.

If compact output wins, the full raw output is persisted as a KITT artifact when an `ArtifactStore` is available. The compact response contains its artifact id. No information must become permanently unreachable due only to context optimization.

## Hybrid memory

KITT's existing Dreaming lifecycle remains canonical. The native subsystem augments it instead of creating a second memory product.

Ranking combines:

- lexical overlap;
- vector similarity;
- KITT salience/importance/confidence/access reinforcement;
- direct task overlap;
- pinning.

A deterministic hash-projection encoder is bundled as a dependency-free fallback. It is explicitly not treated as a semantic language model. A real local/provider embedder can implement the `Embedder` protocol without changing storage/query APIs.

Durable knowledge adds:

- concepts with confidence/revision/labels/source-memory provenance;
- typed links (`PART_OF`, `DEPENDS_ON`, `RELATED_TO`, `CONTRADICTS`, `REFINES`, `ALTERNATIVE_TO`, `CAUSED_BY`, `INSTANCE_OF`, `SUPERSEDES`);
- correction memories containing context, predicted behavior, corrected behavior, reason and application count.

A completed non-dry-run Dream refreshes memory vectors and promotes only high-confidence project rules/architecture decisions, preserving the existing Dreaming validator as the trust boundary.

## Multi-agent isolation

Children use two roots:

- `execution_root`: the child's isolated Git worktree;
- `state_root`: the original KITT workspace that owns history, approvals, artifacts, memory and shared runtime state.

This is necessary to isolate code edits without accidentally creating a second KITT workspace/database for each child.

The coordinator provides:

- worktree per child under `.kitt/worktrees/<child>`;
- KITT-owned branch namespace `kitt/child/<child>`;
- READ/WRITE leases with TTL;
- exact symbol write leases for structural edits;
- dependency READ leases when a dependency graph is available;
- path write leases for legacy patch operations;
- serialized integration;
- refusal to merge into a dirty main worktree;
- rebase-before-merge;
- merge/rebase abort on conflict;
- preservation of child branch/worktree on failure for recovery;
- automatic lease release after successful integration.

Non-Git workspaces continue to run using the existing shared-root behavior rather than making child execution unavailable.

## Database migration

Migration 14 adds only KITT-owned extension tables:

- `native_memory_vectors`
- `knowledge_concepts`
- `knowledge_links`
- `correction_memories`
- `coordination_leases`
- `child_worktrees`

The current `memories`, `memory_evidence` and `dream_runs` tables remain authoritative for episodic Dreaming memory.

## Packaging

Development/source installation can keep the current setuptools path and therefore works without Rust by using the Python fallback.

Release CI builds platform wheels with Maturin/PyO3 ABI3. The intended end-user experience remains one KITT installation; Rust/Cargo/Tree-sitter do not need to be installed manually by users who install a supported prebuilt wheel.

Unsupported platforms may run the Python fallback.
