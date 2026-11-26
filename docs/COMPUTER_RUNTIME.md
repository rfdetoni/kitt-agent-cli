# Computer runtime selection and cold pause

The existing ConversationRuntimeManager supports an opt-in host-selected
`AUTO` provision mode. Supply an explicit ordered `auto_backends` list of
permitted backends, e.g. `["PODMAN", "DOCKER"]`, and an image. The first
installed, configured driver is selected before any operation is submitted.
For a remote backend the endpoint must be configured. The existing endpoint
and transport checks still apply. An unrestricted LOCAL backend is never
selected automatically. No fallback is attempted after a provision request
fails, because it may already have produced side effects.

This is a manager-level API, not a new model-facing tool or a new approval
authority. Host policy must authorize the backend and configuration. No
global environment setting silently moves an existing conversation.

For Docker and Podman, `hibernate_on_pause: true` replaces container pause
with stop, and resume with start. This frees process memory and retains the
container writable layer and mounted workspace. Volatile process state is
lost; it is not a VM memory snapshot. The default pause/unpause behavior is
unchanged. Use only for workloads that tolerate restart. This does not
implement automatic idle timers or scheduled hibernation.

The durable binding retains its concrete backend and PAUSED state; a
hibernated container may be resumed after restarting the manager. Never
treat a stopped RUNNING container as a successful health check.

## Operator RPC

The local host-owned `kitt rpc --no-start-services` stdio adapter accepts
`runtime.status`, `runtime.health`, `runtime.provision`, `runtime.attach`,
`runtime.pause`, `runtime.resume`, `runtime.snapshot`, and
`runtime.terminate`. All require an explicit existing conversation ID
within the active workspace. No arbitrary command execution is exposed by
these RPC methods and they are not model-facing tools.

Example request (replace the conversation ID with a real one):

```json
{"jsonrpc":"2.0","id":1,"method":"runtime.provision","params":{"conversation_id":"existing-id","backend":"AUTO","config":{"auto_backends":["PODMAN","DOCKER"],"image":"alpine:3.20","hibernate_on_pause":true}}}
```

Use `runtime.status` for the persisted binding without a backend request;
`runtime.health` explicitly probes the backend. `kitt sessions --json`
includes the sanitized runtime binding metadata, suitable for UI consumers.
Do not expose the local operator RPC directly on an unauthenticated network.

## Resource limits

The operator may specify integer `cpu_millis`, `memory_bytes` and
`pids_limit` when provisioning. Docker/Podman enforce the requested caps
and optionally disable networking with `network_disabled: true`. Kubernetes
sets CPU/memory resource requests and limits; PID and network isolation require
cluster policy and are rejected rather than silently ignored. LOCAL rejects
these options. AUTO excludes local containers with insufficient observed
CPU or memory, accounting for CPU affinity and Linux cgroup v2 where available.
This is a preflight estimate, not a reservation or a remote capacity handshake.

## Optional WASM compute

Install `wasmtime==51.0.0` in the Agent Python environment and explicitly
set `KITT_WASM_ENABLED=1`. `sandbox.wasm` requires the safe-compute
capability and the existing compute policy, and is available to bounded
CodeMode programs. It accepts a WAT module (32 KiB maximum), integer
arguments, a single integer result, fuel (up to 1 million), and linear
memory (up to 16 MiB). No WASI, imports, host functions, or shared state.
A fresh isolated Python worker has a five-second wall-time limit; the
linear-memory limit is not an OS-level RSS limit for the compiler.
The optional engine is not installed automatically.

## Durable execution and idle maintenance

Manager execution accepts an optional request_id, scoped to a conversation.
A durable claim precedes backend dispatch. Completed retries return a
receipt (not duplicate output); uncertain retries return INDETERMINATE
without replaying commands. Receipts are bounded by the existing state
quota and are not automatically evicted. A process crash may leave an
active operation fence; inspect `runtime.operation_status` and use
`runtime.acknowledge_interrupted` only after confirming independently
that no backend operation is active. Never assume a timeout cancelled a
remote operation. Runtime config and binding are persisted together.

For Docker/Podman set `hibernate_on_pause: true`,
`idle_hibernate_seconds: 300` and optionally `auto_resume: true`.
The existing wake scheduler calls registry.hibernate_idle() at most every
30 seconds while it is enabled; this uses the same durable operation fence
as execution. Manual RPC
`runtime.hibernate_idle` also checks the idle budget. With auto_resume
enabled, the next manager execution resumes a paused runtime, including
a manually paused one. No separate daemon is created. The optional WASM
engine is installed in CI to exercise real fuel and import guards.

## Bounded artifact transfer

The host-owned artifact store accepts binary chunks via `put_stream`, spools
large data to disk, verifies an optional expected SHA-256 before publication,
and deduplicates verified blobs. `transfer_to` copies an artifact between two
already authorized stores with identical workspace/conversation scope and
preserves sensitivity and expiry metadata. Transfers use 64 KiB reads. This is
a local/shared-store primitive, not a network cache or remote authorization API.

## Read-only operator mounts and compressed frames

Docker and Podman provisioning accept `readonly_mounts`, an object mapping
up to eight simple aliases to absolute, existing operator-selected host directories.
They are mounted at `/mnt/kitt/<alias>` with read-only flags. These are live bind
mounts, not snapshots or a model-facing VFS. AUTO refuses to choose unsupported
backends when mounts are required. Do not select directories containing credentials.

`ArtifactStore.export_frames` and `import_frames` provide independent 64 KiB
transport frames with optional native Python 3.14 Zstandard level 1 compression.
Uncompressible blocks remain raw. Import validates frame size, decompression limits,
and the expected full SHA-256 before publishing. The caller supplies authorized
workspace/conversation metadata; no unauthenticated network endpoint is created.

## Remote artifact cache (operator RPC)

After binding a dedicated Assistant REMOTE worker, use `runtime.artifact_push`
with an explicit conversation_id and artifact_id. Non-NORMAL artifacts require
allow_sensitive=true from the operator. HEAD checks for an existing verified
blob before streaming a PUT; the worker validates the full SHA-256. The response
includes the read-only cache path for the remote container. `runtime.artifact_pull`
accepts a SHA-256 and creates a scoped SENSITIVE local artifact, validating the
complete hash before publication. No automatic synchronization or model access.
Only raw blobs are transported over this worker protocol; Zstd remains a separate
codec. Both directions are limited to 8 MiB and use the existing bounded HTTP
transport, rejecting redirects. Remote execution receipts now carry a scoped
request ID to the worker when supplied by the local caller.

## Explicit model-facing remote execution

The operator may provision a dedicated authenticated REMOTE worker with
`agent_execute: true`, `network_disabled: true` and a dedicated
`KITT_REMOTE_RUNTIME_TOKEN` environment reference. `computer.status` returns
an opaque binding identity (runtime ID, config digest and creation time);
`computer.run` accepts exactly argv, that binding identity and a unique
request_id within the turn. The model must have process.run and repo.write
authority and an unscoped root principal. Each invocation requires a
single-use approval even in autonomous/allow-all policy. The binding is
rechecked under the operation fence immediately before dispatch. No automatic
local fallback, host directory transfer, network elevation, child path-scope
bypass or task-transaction bypass. Remote output is not evidence of local
workspace validation. The remote worker is operator-trusted and must enforce
its documented isolation; this does not turn arbitrary remote URLs into
trusted execution targets.
