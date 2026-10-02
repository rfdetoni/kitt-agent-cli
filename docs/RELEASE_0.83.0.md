# Agent CLI 0.83.0 — 2026-10-02

The execution wallet remains owned by Agent CLI. A proxy request receives a reserved upstream-attempt grant (at most three), a cumulative input allowance and the remaining whole-turn duration. HTTP retries reuse request identity and do not reserve new upstream attempts. Usage refunds unspent calls; absent acknowledgements retain conservative reservations. Standalone client calls grant one attempt. The proxy owns bounded semantic/serialization repairs; the adapter no longer starts an extra semantic retry behind the wallet.

Processing locality comes from the model backend and endpoint, independently of a local gateway. Web proxy execution is remote, including explicit profile selection, review and evolution. Offline/local-only policies reject it.

Cancellation shuts down active HTTP sockets and interrupts retry waits and pending async writes. DNS/TCP/TLS setup remains bounded by ten seconds; this is not a guarantee that remote computation stops. Cancelling an acknowledged transport does not undo submitted provider side effects.

Required context bodies are costed as serialized JSON, including metadata. Required structured bodies that cannot fit fail before dispatch; textual bodies are bounded with their metadata retained. User intent remains provenance and is already carried by the original user message.

File reads preserve UTF-8, CRLF and final newlines. Resume with `next_start_byte` as `start_byte`; supply `full_file_hash` as `expected_file_hash` to reject changes between pages. Existing line bounds remain supported.

Memory reconnect/startup retries happen only before a connection. Post-send timeouts, validation/authentication errors and correlation failures are terminal and expose `request_id`. Memory 0.8 adds `request.status` reconciliation; `not_found` after receipt pruning is not proof a request never executed. Receipt persistence belongs to Memory, not the Agent client.

## Security and performance review

| Severity | Confidence | Location | Problem / impact | Fix | Validation |
| --- | --- | --- | --- | --- | --- |
| High | High | llm/privacy; turn_model; goals; Dreaming | Loopback proxy bypassed local-only processing policy | Shared model-locality predicate, explicit proxy gate | Gateway hardening and routing tests |
| High | High | execution_budget; turn_model; proxy adapter | Hidden attempts could exceed wallet | Atomic grants and cumulative input reservation, usage settlement | Budget and retry accounting tests |
| Medium | High | http_security; client; turn_processor | Cancelled consumers retained stalled network workers | Socket shutdown, cancellable waits and queued puts | Real stalled HTTP regression |
| Medium | High | context/envelope | Mandatory structured context exceeded body budget | Serialized body accounting and explicit rejection | Mandatory structured context regression |
| Medium | High | shared_client | Any Memory error triggered replay/startup | Pre-connect-only retry, stable IDs and one deadline | Terminal Memory regression and existing IPC tests |
| Medium | High | tools/handlers/files; native/bridge | Long lines lost tails and delimiters | Exact byte cursor and optional snapshot hash | UTF-8/CRLF roundtrip and stale-page rejection |

No new Python runtime dependency. WorkspaceFileSystem remains the containment authority. Broad checks: pytest, compileall, clean-room packaging and strict unused-symbol/security lint. Cross-component native validation belongs to Toolbox.
