# Agent CLI 0.88.12 — operator-only MCP for RE guests

Adds `python -m kitt.re_guest_mcp --role operator`, a stdio Model Context Protocol server wrapping existing operator-owned RE lab status and the pinned Windows Oracle readiness check. Only two tools are exposed: `guests_status` and `windows_oracle_doctor`. No guest shell, filesystem edits, Docker exec, network server or arbitrary probe execution. Responses redact host paths and container IDs and always declare guest attestation unverified.

A model-supplied SHA-256 must match an operator-pinned Windows manifest for readiness; the MCP adapter never supplies privileged host credentials or raw probe contents. This server is **not** enabled automatically and must never be made available to the isolated Wine Implementer. See [RE guest MCP](RE_GUEST_MCP.md). Synthetic stdio, argument validation, guest metadata redaction and forbidden tool tests added.

No changes to existing Clean-Room behavior contract or Agent v5 wire; all pin/lock versions aligned at 0.88.12. Live guest end-to-end certification is not claimed.
