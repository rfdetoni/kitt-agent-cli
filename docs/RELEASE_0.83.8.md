# Agent CLI 0.83.8 — canonical Reverse Proxy transport

## Scope

This patch consolidates the Agent CLI ↔ Reverse Proxy request contract without creating a Protocol v2. KITT Protocol 0.9.0 remains authoritative for `ContextEnvelope` and `KittRequestMetadata`.

## Invariants

- Agent → Proxy tools are sent only through OpenAI `tools/tool_choice`.
- Typed execution context is carried through `kitt_context`; correlation and routing stay in `kitt_meta`.
- Legacy `functions/function_call` request fields are not emitted.
- Textual `Tool Contract` is not used as capability authority.
- Native Proxy `tool_calls` are converted once into the Agent's canonical internal `<kitt-tool>` bridge, preserving call id, tool name, JSON arguments and bounded public `reasoning_summary`.
- Recoverable Proxy contract errors continue through the existing same-session continue/retry path.

## Validation target

`python scripts/run_release_critical.py`, including `tests/test_kitt_reverse_proxy_compat.py`.
