# Agent CLI v0.87.28 — no automatic replay after ambiguous WebChat capture

## Evidence
Agent log 2026-10-15 13:44–13:51 and reverse-proxy-chatgpt-3000 log: planning took 242.6s and completed with a validated KAP/2 plan. The first durable task T00 was dispatched at 13:48:32; the proxy confirmed prompt submission but found zero new assistant-owned nodes / zero fresh KAP candidates; it returned `ui_timeout` three times. The agent scheduler reran the exact T00 prompt without a new operator send_input. No KAP action was received/executed.

## Fix
- Classify only explicit KITT reverse-proxy `ui_timeout` with `No KAP action was received or executed` as an ambiguous browser capture.
- Mark that durable contract item `BLOCKED` with `block_reason=ENVIRONMENT`. No automatic replay or retry-budget depletion; previously completed items retained.
- The operator must inspect/reset/re-authenticate the existing browser session (or select a working model) and explicitly resume. Do not parse older K2/END markers or assume DOM mutations are a fresh response.
- Add targeted predicate + durable scheduler regression for no requeue and explicit resume.
- Version and uv.lock updated; no new dependency, protocol or database migration.

## Remaining limitation
The root cause of missing DOM assistant replies cannot be established from these logs alone. The proxy reports 0 assistant nodes and no fresh content even though generation stopped; without a live authenticated browser DOM trace it is unsafe to guess selectors or fabricate a response. Proxy memory-pressure warnings above its configured 768 MiB resident budget are corroborating telemetry, not proof of why the response was missing.
