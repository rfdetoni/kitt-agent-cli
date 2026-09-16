"""System prompts and provider-bound prompt normalization."""

_CONTEXT_SUMMARY_PREFIX = "Prepare a short technical context for another model to answer the task."

CONTEXT_SUMMARY_SYSTEM = (
    f"{_CONTEXT_SUMMARY_PREFIX} "
    "Use only facts from the project map. Cite relevant files, components, and relationships. "
    "Do not answer the task, do not use or request host tools, do not use agent identity, and do not expose reasoning. "
    "Return only the context summary. Maximum: 12 lines."
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
    "FILE/CODE MUTATION PROTOCOL: any generated source code, configuration, script, markup, "
    "or other file body that is intended to exist in the workspace MUST be placed inside the "
    "arguments of an actual host mutation tool call, never emitted as ordinary assistant text "
    "or a Markdown code fence as a substitute for writing the file. When creating or replacing "
    "a file, call the available write/create mutation with the target path and the complete file "
    "content in its arguments. When editing an existing file, call the available edit/patch "
    "mutation with the exact edit or patch payload in its arguments. Do not print a file body "
    "first and promise to apply it later. When a write/edit tool is available, never substitute "
    "pasted implementation code or prose for the required workspace mutation. A final answer "
    "may describe completed work only after the host has returned evidence that the required "
    "mutation tool calls succeeded. Never claim that you cannot create or modify workspace "
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

    Context-summary calls are canonicalized before tool-contract handling so an
    accidentally appended Tool Contract can never promote a summary subcall to
    an execution-agent turn. Tool-enabled execution turns still receive the
    neutral execution-agent contract. Legacy name-addressed chat prompts without
    a tool contract are normalized back to the ordinary concise persona so
    mentioning "KITT" cannot change routing, autonomy, or tool behavior at the
    provider boundary.
    """
    if not system_prompt:
        return system_prompt

    text = system_prompt.strip()
    if text.startswith(_CONTEXT_SUMMARY_PREFIX):
        return CONTEXT_SUMMARY_SYSTEM

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
