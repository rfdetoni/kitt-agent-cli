# kitt-agent-cli 0.84.10 — WebChat owns token limits

The supplied Gemini logs show a valid eight-item contract returned in approximately 13 seconds, followed by a second Agent turn and no second provider request. Replaying the objective and returned contract reproduced `Required structured context exceeds the token budget`: Agent treated its local 8K profile placeholder as WebChat capacity.

Reverse-proxy execution now delegates input and output token limits to WebChat. Prompt allocation and typed envelope compilation preserve the original request and required schemas without a local context ceiling or output reserve. Follow-up messages and tool observations are not trimmed against profile token placeholders. The Agent does not send max_tokens or max_prompt_tokens to the proxy. Automatic history compaction is not triggered by a guessed WebChat context size.

Per-turn, child and durable-goal token quotas do not enforce limits when the selected provider is the reverse proxy. Token usage remains observable. Model/tool calls, retries, duration, costs, subagent counts, cancellation, approvals and byte/path bounds retain their controls. Context retrieval remains selective; the complete repository is not loaded by convenience. Native/API providers retain their existing token budgets.

The UI displays WebChat ownership instead of a fabricated local capacity percentage. Input accounting counts USER_INTENT once while retaining its typed provenance segment. Prompt preparation records success/failure, and terminal exceptions emit scoped diagnostics containing the exception type without request or exception content.

## Validation

A real TurnProcessor regression completes planning and required high-risk review with requests larger than the local 8K profile and per-turn token quotas of one. It verifies the complete objective and contract, no max_prompt_tokens metadata, and no local output reserve. Other regressions cover full tool observations, usage reconciliation above configured quotas, child usage, durable-goal provider changes, preserved operational limits, native-provider rejection and WebChat UI rendering. Adapter checks require max_tokens to be absent from the wire.

Local validation: 269 Agent tests passed with one skipped, plus compilation, critical Ruff checks and clean-room provenance verification. A fresh authenticated Gemini browser conversation was not run; the provided response was replayed locally.
