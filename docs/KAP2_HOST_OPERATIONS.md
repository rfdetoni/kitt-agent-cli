> Current wire format: JSON with Agent contract v5. See [0.88.0](RELEASE_0.88.0.md). Examples below describe archived releases and must not be used in active prompts.

# KAP/2 host extensions — Agent CLI

These operations are **host-mediated KAP/2 tool actions**, not changes to the
KAP/2 core grammar or permissions. They can be used with an ordinary
`K2 T kitt_runtime` envelope (one host action per model turn). The existing
`KITT/1` and JSON HTTP transport remain independent.

## KAP/2: `sandbox.python`

Use the existing KAP/2 literal block, without any protocol version change:

```text
K2 T kitt_runtime
operation: sandbox.python
arguments:
  code<<PY
values = [10, 20, 30, 40]
_result = {"average": sum(values) / len(values), "total": sum(values)}
PY
K2/END
```

The operation delegates to the existing `python_compute` AST-only interpreter
with the `compute.safe` capability, normal tool policy and bounded worker
process. It returns structured `stdout`, `result` and `steps`. Optional
`inputs` is a JSON-compatible object; `result_var` defaults to `_result`.
Imports, open/filesystem, networking, shell, arbitrary Python execution and
user-defined functions/classes remain unavailable. This MVP adds neither Python
sessions nor Python batches; it grants no access to privileged host tools.

## Host capability discovery

Call `kap.capabilities` with an empty argument object to learn which
KAP/2 host operations are actually activated. The response includes
`grammar: KAP/2`, the fixed positional schema digest and explicit
`false` flags for frame-level batching, early-stream termination and
cross-transport metadata version negotiation. This is an introspection
action, not a grant to enable unsupported syntax or bypass policy.

## Independent read-only batches

`repo.batch_read` takes 1–8 calls with unique IDs. Allowed operations are
`repo.read` and `repo.list` only. Input is validated **before any call**,
each subcall executes through the original capability and security checks,
and calls run with at most four workers. Output is ordered and bounded to
64 KiB. An error or approval requirement cannot silently become success.

```text
K2 T kitt_runtime
operation: repo.batch_read
arguments:
  calls[2]:
    -
      id: a
      operation: repo.read
      arguments:
        path: README.md
    -
      id: b
      operation: repo.read
      arguments:
        path: pom.xml
K2/END
```

For a more compact positional **table** (`calls[2]{id,operation,path}`),
the host additionally requires the exact SHA-256 digest of the fixed schema
string `repo.batch_read/v1|id,operation,path`. Calculate with
`python -c "import hashlib; print(hashlib.sha256(b'repo.batch_read/v1|id,operation,path').hexdigest())"`.
The parser reconstructs the named row fields and the host maps each row
to one `repo.read`. Invented digests, fields, orders and write operations
are rejected before any execution. No dynamic model-defined schema.

## Revision references and write receipts

`repo.ref.create` takes `{path}` and returns an opaque `ref`,
SHA-256 `revision`, byte count and TTL of one hour. The reference lives
in the **existing persistent conversation/workspace-scoped state store**.
`repo.ref.read` takes `{ref, max_bytes?}`, revalidates the SHA-256 and
rejects stale or expired references. Both have a 64-KiB per-file limit.

`repo.ref.write` takes `{ref, path, expected_revision, content}`.
The explicit path participates in normal policy and control-plane checks.
The host requires a scoped `ExecutionSecurityContext`, verifies the
reference path and expected hash, and delegates an atomic compare-and-swap
write to `WorkspaceFileSystem`. On success it revokes the handle and
returns the previous and committed hashes as a **write receipt**. Missing
approval, stale hashes, replay, mismatched paths, symlinks and out-of-scope
paths fail closed. This operation does not accept generic model patches.

## Differential plan updates

`plan.patch` uses the **existing TaskPlanCoordinator and EventLedger**.
Parameters: `{expected_revision: integer, changes: [{local_id, title?,
paths?, check_ids?, depends_on?}]}`. Changes are limited to undelegated
PENDING tasks. Existing IDs, verification evidence, attempts, child leases
and other execution state cannot be patched. Paths and dependency graphs
are revalidated, including cycles and scope. The host checks the existing
revision and creates the next immutable event. Distinct concurrent
commits of the same revision share a deterministic event ID and are
rejected by the ledger's unique-key semantics.

## Compatibility and limits

- The host owns all permissions and performs independent verification;
  the textual envelope never authorizes a write.
- These APIs are available in the Python Agent CLI. No separate `K2 B`
  grammar or tool execution route in the reverse proxy is required.
- Exact request-header negotiation for the deployed `KAP/2` version is active
  through `X-Kitt-KAP` on Agent+Proxy HTTP contract requests. Unrecognized
  versions are rejected, without fallback. New `K2/n` grammar revisions are
  **not** implemented and require their own coordinated capability release.
- Streaming completion still depends on trusted transport data or a
  complete strictly parsed assistant-owned UI response; a terminator in
  an arbitrary DOM node is not sufficient.
- Concurrency is capped and only read-only; the browser proxy itself
  remains a serialized UI executor.
- Read reference files are capped at 64 KiB, and a bounded read may be
  truncated; it is not an artifact mechanism for arbitrary binaries.

## Compact planning finals — 0.87.11

Plans may be expressed as `K2 F @json: {"items":[...]} K2/END`, an
explicit indentation-free KAP/2 final validated by Protocol 0.13.3.
Tools remain `K2 T kitt_runtime`. A flattened legacy `items[n]`
frame instead returns `E_LAYOUT_LOSS` for a new model response, never
guessing a plan.
