# kitt-agent-cli 0.84.10 — High-risk contract review context

The Gemini logs from 2026-10-08 show a valid eight-item contract returned in approximately 13 seconds, followed by prompt preparation for a second turn and no second provider request. Replaying the objective and returned contract reproduced `Required structured context exceeds the token budget` in the pre-mutation review. The generic coding output reserve consumed 4,096 tokens of an 8,192-token context, starving the required tool schema.

Contract reviewers now reserve at most 2,048 output tokens and request at most 12 concise issues. The original objective, validated contract and required policy context remain intact; high-risk review, host checks and read-only authority remain enforced. The tool loop passes its output ceiling through to the LLM client, which caps it against the configured profile without changing the shared client. Existing providers and callers retain their default when no override is provided.

Input accounting excludes the provenance-only USER_INTENT segment because the proxy already receives intent in the user message. The envelope still retains that segment for audit and reconciliation. Prompt preparation records an explicit success/failure status, and terminal exceptions emit a scoped `turn.failure` diagnostic containing the exception type without request or exception content.

## Validation

A regression exercises both planning and review through the real TurnProcessor with an 8K Gemini profile and a deterministic provider fixture. It failed before the fix and now reaches review, preserves the complete objective and items, and stays within input-plus-output bounds. A second regression verifies scoped diagnostics and terminal failure for an oversized request. Existing client integration checks cover per-request output reduction, default behavior, configured ceilings and shared-client immutability.

Local validation: 266 tests passed, one skipped, plus compilation, critical Ruff checks and clean-room provenance verification. The supplied response was replayed locally; a fresh authenticated Gemini browser conversation was not executed.
