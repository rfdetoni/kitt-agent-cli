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
