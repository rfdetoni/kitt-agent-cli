# Security Threat Model & Invariants

## Trust Boundaries & Protection Matrix

| Trust Boundary | Threat | Mitigation & Security Invariant |
|---|---|---|
| **LLM Tool Arguments** | Prompt injection into path or commands | Parameters validated by `WorkspacePathPolicy`, `PolicyEngine` and `ApprovalManager`. No unquoted shell execution. |
| **SafeRuntime Inner Ops** | Bypass of policy via composite `kitt_runtime` tool | Every inner operation declares required capability, verifies caller effective capabilities, calls `PolicyEngine` / `ApprovalManager` per operation, enforces `PathPolicy`. |
| **Executable Skills** | Arbitrary Python execution (`import os`, `subprocess`, `eval`, `exec`) | Restricted execution via AST analysis / safe environment proxy. Block forbidden builtins, reflection, sys.modules manipulation and filesystem escape. |
| **Child Agents** | Privilege escalation | `compute_child_privileges`: `child_capabilities = requested & parent_capabilities & policy_allowed`. Child can never obtain permissions superior to parent. |
| **Child Messaging** | Cross-workspace / cross-parent eavesdropping or spoofing | Messages validate common parent, workspace boundary, and sender/recipient existence in active conversation. |
| **Daemon IPC** | Unauthorized local access or hijacking | 256-bit cryptographically secure token stored with strict 0600 file permissions and owner verification. Unix socket mode 0600. No external network binding by default. |
| **Scheduler** | Uncontrolled loops or DoS | Strict bounded limits: `max_turns`, `max_tokens`, `max_wall_time`, `max_cost`, `max_failures`, `max_retries`. Exceeding limits transitions state to `PAUSED_BUDGET_EXCEEDED`. |
| **Context Handles** | Accessing unauthorized artifacts/children/goals | Resolution enforces workspace and conversation scoping. Path handles pass through `WorkspacePathPolicy`. |
| **Runtime State** | State exhaustion DoS / artifact bomb | Strict bounds: max 64KB per entry, max 512KB per session, max 100 entries, automated TTL expiration. |
| **Plugin / MCP Calls** | Malicious or compromised MCP tool | MCP calls validated against capability broker, egress policy, and policy engine permissions. |
