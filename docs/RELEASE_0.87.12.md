# Agent CLI 0.87.12 — host tool execution before Goals completion reports

Incident 2026-10-14 19:00–19:05: reverse-proxy 5.3.11
submitted all T00 prompts successfully, but returned five K2 F
INCOMPLETE reports and zero host tool calls. The model claimed that
its ChatGPT Web sandbox lacked the operator's KITT workspace, even
though the Agent explicitly advertised kitt_runtime and the daemon
had selected the operator workspace.

Cause: the Goals completion prompt required exactly K2 F as the
response to every turn, contradicting K2 T tool-based implementation.
The agent-loop also exempted Goals with an outer verifier from its
host-state finalization gate and accepted zero-tool finals.

Fix:
- The completion contract now instructs the model to request K2 T
  host tools before K2 F reporting, distinguishing WebChat sandbox
  from the authorized KITT workspace.
- For outer-verified durable Goals only, a model-only final before
  any host tool receives up to two directed KAP/2 tool reminders.
  If the model still refuses, the host blocks with evidence instead
  of repeatedly treating model reports as task progress.
- No automatic tool executions are invented. Existing tool policy,
  approvals, schemas and host verification remain authoritative.

The first operational check should be an actual read-only host operation
(repo.list/repo.read/process.run if authorized), with tool receipt
and durable progress visible before any K2 F report. Browser/WebChat
manual confirmation still required; CI is not a live browser test.
