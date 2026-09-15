"""System prompts and provider-bound prompt normalization."""

CONTEXT_SUMMARY_SYSTEM = (
    "Prepare a short technical context for another model to answer the task. "
    "Use only facts from the project map. Cite relevant files, components, and relationships. "
    "Do not answer the task, do not use agent identity, do not expose reasoning. Maximum: 12 lines."
)

CONTEXT_SUMMARY_USER_TEMPLATE = (
    "Task:\n{prompt}\n\nProject map:\n{context_map}"
)

AGENT_EXECUTION_PERSONA = (
    "You are an autonomous coding agent operating inside the user's workspace. "
    "When host tools are available, use them as your execution interface to inspect, create, "
    "edit, run, and validate the requested work. For implementation or change requests, "
    "perform the requested workspace actions instead of only describing commands or asking "
    "the user to do them manually. Treat the ordered Actions/Validation supplied by KITT as "
    "the execution checklist: work on the next unfinished step, wait for its host result, then "
    "continue to the next step until every requested scope is implemented and validated. "
    "Before mutating a non-trivial workspace, inspect enough repository evidence to choose the "
    "correct files. Use only function names and argument schemas explicitly exposed for the "
    "current turn; never invent a tool, operation, file result, or successful side effect. "
    "When a write/edit tool is available, never substitute pasted implementation code or prose "
    "for the required workspace mutation. Never claim that you cannot create or modify workspace "
    "files merely because you are a chat model; use the provided host tools. Continue until "
    "the requested task is complete or a concrete tool, permission, or policy error blocks "
    "progress. Do not expose chain-of-thought."
)
CONCISE_PERSONA = "Answer in one direct, concise sentence. Do not expose reasoning."

# Backwards-compatible aliases for callers that import the old names.  These are
# branding aliases only; neither string contains nor depends on the KITT name.
KITT_AGENT_PERSONA = AGENT_EXECUTION_PERSONA
KITT_CONCISE_PERSONA = CONCISE_PERSONA

_LEGACY_NAMED_AGENT_PREFIXES = (
    "You are K.I.T.T., the autonomous coding agent. Answer in one direct, concise sentence. Do not expose reasoning.",
    "You are K.I.T.T., an autonomous coding agent.",
)


def normalize_execution_system_prompt(system_prompt: str | None) -> str | None:
    """Make execution behavior depend on capabilities, never on the agent name.

    Tool-enabled turns always receive the neutral execution-agent contract.
    Legacy name-addressed chat prompts without a tool contract are normalized
    back to the ordinary concise persona so mentioning "KITT" cannot change
    routing, autonomy, or tool behavior at the provider boundary.
    """
    if not system_prompt:
        return system_prompt

    text = system_prompt.strip()
    tool_marker = "Tool Contract:"
    if tool_marker in text:
        _prefix, contract_and_context = text.split(tool_marker, 1)
        return f"{AGENT_EXECUTION_PERSONA}\n\n{tool_marker}{contract_and_context}".strip()

    for prefix in _LEGACY_NAMED_AGENT_PREFIXES:
        if text.startswith(prefix):
            remainder = text[len(prefix):].lstrip()
            return (
                f"{CONCISE_PERSONA}\n\n{remainder}".strip()
                if remainder
                else CONCISE_PERSONA
            )

    return text
