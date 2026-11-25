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
