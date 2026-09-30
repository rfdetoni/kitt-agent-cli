"""Per-turn context envelope for the strict kitt-reverse-proxy agent contract."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple



AGENT_CONTRACT_HEADER = "X-Kitt-Agent-Contract"
AGENT_CONTRACT_VERSION = "v2"
AGENT_ROUTE_HEADER = "X-Kitt-Route"
TURN_CONTEXT_MARKER = "[KITT TURN CONTEXT]"
TURN_CONTEXT_END_MARKER = "[END KITT TURN CONTEXT]"
UNTRUSTED_WORKSPACE_LABEL = "UNTRUSTED_WORKSPACE_DATA"
SUPPORTED_ROUTES = frozenset(
    {
        "context-gather",
        "summarize",
        "code-generation",
        "code-edit",
        "validate-diff",
        "agent-loop",
        "chat",
    }
)

_TOOL_NAME_RE = re.compile(r"['\"]name['\"]\s*:\s*['\"]([A-Za-z0-9_.:-]{1,64})['\"]")
_CONTEXT_SUMMARY_PREFIX = "Prepare a short technical context for another model to answer the task."
_INTERNAL_TOOL_FEEDBACK_PREFIXES = (
    "the host tool call is invalid (",
    "the python_compute call is invalid (",
    "apply_patch was rejected before approval:",
)
_INTERNAL_ROUTING_MARKERS = (
    "[kitt forward progress required]",
    "[kitt execution required]",
    "[kitt completion verification]",
    "[kitt completion contract]",
    "[kitt contract repair]",
)
_MUTATION_RECOVERY_MARKERS = (
    "[kitt execution required]",
    "[kitt completion contract]",
)
_MUTATION_RECOVERY_TERMS = (
    "implementation",
    "implementação",
    "implementacao",
    "workspace",
    "mutation",
    "mutação",
    "mutacao",
    "incomplete",
    "incompleto",
    "incompleta",
)
_HOST_TOOL_RESULT_MARKER = " result from the host."
_TOP_LEVEL_HEADERS = (
    "Tool Contract:",
    "Memory:",
    "Active Skills:",
    "Project Guidelines:",
    "Formatting Contract:",
    "Learned Harness:",
    "Mandatory Constraints:",
    "Files Context:",
    "Repo Map:",
    "Recent Conversation:",
    "Project context:",
    "WORKSPACE_CONTEXT:",
    "Workspace context:",
    "[PLANNING MODE ACTIVE]",
)
_UNTRUSTED_HEADERS = frozenset(
    {
        "Active Skills:",
        "Project Guidelines:",
        "Formatting Contract:",
        "Files Context:",
        "Repo Map:",
        "Recent Conversation:",
        "Project context:",
        "WORKSPACE_CONTEXT:",
        "Workspace context:",
    }
)
_HEADER_RE = re.compile(
    r"(?m)^(" + "|".join(re.escape(header) for header in _TOP_LEVEL_HEADERS) + r")"
)


def normalize_agent_route(route: Optional[str]) -> str:
    """Return a supported router contract name, defaulting to ordinary chat."""
    value = (route or "chat").strip()
    if value not in SUPPORTED_ROUTES:
        raise ValueError(f"Unsupported KITT agent route: {value!r}")
    return value


def _is_internal_tool_feedback(content: Any) -> bool:
    """Return True for KITT-generated continuation feedback, never user task intent."""
    text = str(content or "").strip().casefold()
    if not text:
        return False
    if _HOST_TOOL_RESULT_MARKER in text[:512]:
        return True
    if any(text.startswith(marker) for marker in _INTERNAL_ROUTING_MARKERS):
        return True
    return any(text.startswith(prefix) for prefix in _INTERNAL_TOOL_FEEDBACK_PREFIXES)


def _routing_user_messages(messages: List[Dict[str, Any]]) -> List[str]:
    """Return real user task messages in chronological order, excluding KITT feedback."""
    result: List[str] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        if _is_internal_tool_feedback(content):
            continue
        result.append(content)
    return result


def _latest_routing_user_message(messages: List[Dict[str, Any]]) -> str:
    """Find the latest real user task, skipping KITT-generated continuation envelopes."""
    routable = _routing_user_messages(messages)
    return routable[-1] if routable else ""


def infer_agent_route(
    system_prompt: Optional[str], messages: List[Dict[str, Any]]
) -> str:
    """Infer only protocol-level routing; natural-language intent belongs to the LLM."""
    prompt = system_prompt or ""
    if prompt.startswith(_CONTEXT_SUMMARY_PREFIX):
        return "summarize"
    if "[PLANNING MODE ACTIVE]" in prompt:
        return "context-gather"

    tool_contract = ""
    if "Tool Contract:\n" in prompt:
        tool_contract = prompt.split("Tool Contract:\n", 1)[1]
        if "\n\nMemory:\n" in tool_contract:
            tool_contract = tool_contract.split("\n\nMemory:\n", 1)[0]

    tool_names = list(dict.fromkeys(_TOOL_NAME_RE.findall(tool_contract)))
    return "agent-loop" if tool_names else "chat"


def compact_reverse_proxy_orchestration(system_prompt: Optional[str]) -> Optional[str]:
    """Keep only trusted incremental orchestration for the reverse proxy.

    The reverse proxy already receives tool schemas structurally and owns the
    strict execution persona. Forwarding the Agent persona and textual Tool
    Contract again creates a superprompt and wastes the provider context.
    """
    if not system_prompt:
        return system_prompt

    markers = (
        "Memory:",
        "Learned Harness:",
        "Mandatory Constraints:",
        "[PLANNING MODE ACTIVE]",
        "[KITT EXECUTION SLICE:",
    )
    positions = sorted(
        (position, marker)
        for marker in markers
        if (position := system_prompt.find(marker)) >= 0
    )
    parts: List[str] = []
    for index, (start, marker) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else len(system_prompt)
        section = system_prompt[start:end].strip()
        payload = section[len(marker):].strip()
        if payload or marker.startswith("[KITT "):
            parts.append(section)
    compact = "\n\n".join(parts).strip()
    return compact[:4096] or None


def split_workspace_context(system_prompt: Optional[str]) -> Tuple[Optional[str], Any]:
    """Move repository-derived sections out of the provider system prompt.

    Tool contracts, KITT memory/harness constraints and explicit planning mode stay in
    orchestration context. Repository files/maps/guidelines, workspace skills and the
    embedded recent conversation are data and are emitted separately with an explicit
    UNTRUSTED_WORKSPACE_DATA trust label.
    """
    if not system_prompt:
        return system_prompt, "not_provided"

    matches = list(_HEADER_RE.finditer(system_prompt))
    if not matches:
        return system_prompt, "not_provided"

    trusted_parts: List[str] = []
    untrusted_sections: List[Dict[str, str]] = []
    if matches[0].start() > 0:
        trusted_parts.append(system_prompt[: matches[0].start()].rstrip())

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(system_prompt)
        section = system_prompt[match.start() : end].strip()
        header = match.group(1)
        if header in _UNTRUSTED_HEADERS:
            body = section[len(header) :].strip()
            if body:
                untrusted_sections.append({"section": header[:-1], "data": body})
        elif section:
            trusted_parts.append(section)

    orchestration = "\n\n".join(part for part in trusted_parts if part).strip()
    workspace_context: Any = (
        {
            "trust": UNTRUSTED_WORKSPACE_LABEL,
            "source": "kitt-agent-cli",
            "sections": untrusted_sections,
        }
        if untrusted_sections
        else "not_provided"
    )
    return orchestration or None, workspace_context


def inject_agent_turn_context(
    messages: List[Dict[str, Any]],
    workspace_context: Any,
    route: Optional[str] = None,
    *,
    discovery_required: bool = False,
    loop_action_budget: int = 4,
) -> List[Dict[str, Any]]:
    """Prefix volatile turn data to the last real user task without mutating inputs.

    KITT-generated tool feedback must remain byte-stable so the reverse-proxy adapter
    can restore assistant.tool_calls -> tool(tool_call_id) before transport. Keeping
    volatile workspace data on a real user task also gives the proxy a stable logical
    conversation identity after it strips the turn-context envelope.
    """
    payload: Dict[str, Any] = {
        "workspace_context": workspace_context,
    }
    normalized_route = normalize_agent_route(route) if route is not None else None
    if normalized_route is not None:
        payload["route"] = normalized_route
    if normalized_route == "agent-loop":
        payload["loop_action_budget"] = max(1, min(int(loop_action_budget), 32))
    if normalized_route in {"code-generation", "code-edit"}:
        payload["execution_plan"] = ["discovery", "mutation", "validation"]
        payload["execution_phase"] = "discovery" if discovery_required else "mutation"
    if discovery_required:
        payload["discovery_required"] = True

    envelope = (
        f"{TURN_CONTEXT_MARKER}\n"
        f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n"
        f"{TURN_CONTEXT_END_MARKER}"
    )
    cloned = [dict(message) for message in messages]
    for index in range(len(cloned) - 1, -1, -1):
        message = cloned[index]
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if _is_internal_tool_feedback(content):
            continue
        message["content"] = f"{envelope}\n\n{content}" if content else envelope
        return cloned

    cloned.append({"role": "user", "content": envelope})
    return cloned
