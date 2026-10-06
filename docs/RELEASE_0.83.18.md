# Agent CLI 0.83.18

The supplied logs show `ProviderAuthError: Refusing provider egress` for `http://127.0.0.1:3001` after selecting a managed ChatGPT service. The Reverse Proxy role-binding helper saved the new router URL without the endpoint trust granted by the explicit `/model ... <base_url>` path. The default origin on port 3000 masked this omission in earlier topology tests.

Explicit TUI and `/reverse-proxy bind` or `start --role` selections now grant trust to the exact selected origin for `kitt-reverse-proxy`, using the existing private ProviderEndpointTrustStore before router persistence. Invalid roles cannot grant trust, and a failed secure trust write prevents router mutation. Listing, starting without a role, restored workspace configuration, other ports and other provider identities do not gain trust. There is no blanket loopback exemption and no relaxation of credential identity or OAuth binding.

The new regression reproduces the exact port-3001 refusal before correction and asserts the consumer endpoint policy is satisfied before binding; neighboring ports and provider identities stay denied. It is part of the release-critical gate. Package, source fallback and uv lock versions agree. No Protocol wire or dependency changes are necessary.

The earlier connection-refused error at port 3000 is a separate unavailable-service condition. This patch does not launch missing services automatically or replay ambiguous webchat requests. ChatGPT human browser login changes belong to Reverse Proxy 4.9.15.

Validation with the split Assistant runtime loaded: 230 tests and 12 subtests pass, one test is skipped; 217 release-critical tests and 12 subtests pass, compileall and clean-room checks pass. Existing PR Checks also run on fix/** branches, without weakening any gate.
