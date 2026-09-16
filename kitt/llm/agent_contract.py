"""Per-turn context envelope for the strict kitt-reverse-proxy agent contract."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from kitt.router.classifier import TaskClassifier


AGENT_CONTRACT_HEADER = "X-Kitt-Agent-Contract"
AGENT_CONTRACT_VERSION = "v1"
AGENT_ROUTE_HEADER = "X-Kitt-Route"
TURN_CONTEXT_MARKER = "[KITT TURN CONTEXT]"
UNTRUSTED_WORKSPACE_LABEL = "UNTRUSTED_WORKSPACE_DATA"
SUPPORTED_ROUTES = frozenset(
    {
        "context-gather",
        "summarize",
        "code-generation",
        "code-edit",
        "validate-diff",
        "chat",
    }
)

_TOOL_NAME_RE = re.compile(r"['\"]name['\"]\s*:\s*['\"]([A-Za-z0-9_.:-]{1,64})['\"]")
_CONTEXT_SUMMARY_PREFIX = "Prepare a short technical context for another model to answer the task."
_TOP_LEVEL_HEADERS = (
    "Tool Contract:",
    "Memory:",
    "Active Skills:",
    "Project Guidelines:",
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


def infer_agent_route(
    system_prompt: Optional[str], messages: List[Dict[str, Any]]
) -> str:
    """Infer a contract route using KITT's existing TaskClassifier taxonomy."""
    prompt = system_prompt or ""
    if prompt.startswith(_CONTEXT_SUMMARY_PREFIX):
        return "summarize"
    if "[PLANNING MODE ACTIVE]" in prompt:
        return "context-gather"

    tool_contract = prompt
    if "Tool Contract:\n" in prompt:
        tool_contract = prompt.split("Tool Contract:\n", 1)[1]
        if "\n\nMemory:\n" in tool_contract:
            tool_contract = tool_contract.split("\n\nMemory:\n", 1)[0]
    else:
        tool_contract = ""

    tool_names = list(dict.fromkeys(_TOOL_NAME_RE.findall(tool_contract)))
    latest_user = next(
        (
            str(message.get("content") or "")
            for message in reversed(messages)
            if isinstance(message, dict) and message.get("role") == "user"
        ),
        "",
    )
    if tool_names:
        return TaskClassifier().classify_tool_surface(tool_names, prompt=latest_user)
    return "chat"


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
) -> List[Dict[str, Any]]:
    """Add a fresh, structured turn envelope without mutating caller-owned messages."""
    payload: Dict[str, Any] = {
        "workspace_context": workspace_context,
    }
    if route is not None:
        payload["route"] = normalize_agent_route(route)
    context_message = {
        "role": "developer",
        "content": f"{TURN_CONTEXT_MARKER}\n{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}",
    }
    return [context_message, *[dict(message) for message in messages]]
