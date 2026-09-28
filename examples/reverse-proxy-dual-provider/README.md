# Dual-provider Reverse Proxy workflow

KITT can bind different model roles to different Reverse Proxy instances/endpoints.

A common topology is:

```text
Context role    -> Reverse Proxy instance A -> provider/model optimized for context
Principal/Code  -> Reverse Proxy instance B -> provider/model optimized for coding
Validation      -> either A, B or a third compatible route
```

Use the F12 provider/model wizard or the **KITT Reverse Proxy** contextual panel to configure and bind instances. Keep profile credentials inside the Reverse Proxy profile/session boundary; the Agent should only receive the endpoint/model contract it needs.

Validation checklist:

- both instances report healthy;
- Context and Principal/Code roles resolve to the intended endpoints;
- a retained child receives its own stable reverse-proxy session;
- provider transport failures do not broaden workspace permissions;
- stopping one instance produces an explicit degraded/error state rather than silently rebinding to another role.

See [../../docs/REVERSE_PROXY_CONTROL.md](../../docs/REVERSE_PROXY_CONTROL.md).
