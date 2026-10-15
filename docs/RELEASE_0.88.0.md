# Agent CLI 0.88.0 — JSON model responses

Planning, advisory architecture, contract review, independent validation, senior review, completion reports and host-tool recovery now request ordinary JSON. Structured results use the strict shared JSON decoder; textual host actions dispatch from the canonical JSON action object. Duplicate keys, non-finite values and competing objects fail closed.

The proxy adapter uses Agent contract v5 and no longer imports, negotiates or acknowledges KAP. Runtime capability discovery is `runtime.capabilities`, revision references use a runtime-owned namespace, and host approvals, tool schemas, capability limits and execution evidence stay authoritative.

Requires Protocol 0.14.0 and Reverse Proxy 5.4.0. There is no KAP compatibility path in the application.
