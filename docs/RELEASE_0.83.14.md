# K.I.T.T. Agent CLI 0.83.14

Released: 2026-10-05

## Scope

This patch hardens the Agent → KITT Reverse Proxy pre-dispatch path observed in the October 5 Gemini Web logs. The Proxy was authenticated and serving its OpenAI-compatible API, while the Agent stopped after `tool_loop.start` before any `llm.request` or chat request reached the Proxy.

## Runtime changes

- Retry only provider connection failures that are provably pre-accept, such as connection refused, host/network unreachable and temporary DNS resolution failures.
- Keep ambiguous post-transmission failures such as connection-closed/reset non-retryable so a WebChat turn cannot be duplicated.
- Reserve the estimated prompt tokens actually being sent instead of charging the entire remaining reverse-proxy prompt allowance as current input.
- Continue sending the remaining allowance separately as `max_prompt_tokens` to the gateway.
- Emit explicit `tool_loop.dispatch_ready`, `tool_loop.pre_dispatch_error` and `tool_loop.model_dispatch_error` diagnostics so failures before transport are no longer silent.

## Validation

- `tests/test_reverse_proxy_predispatch.py` covers pre-accept connection retry classification and reverse-proxy prompt-budget accounting.
- The regression is part of the release-critical suite.

No KITT Protocol schema or Reverse Proxy contract change is required.
