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

_PROJECT_CONTEXT_MARKERS = (
    "Project context:\n",
    "WORKSPACE_CONTEXT:\n",
    "Workspace context:\n",
)
_TOOL_NAME_RE = re.compile(r"['\"]name['\"]\s*:\s*['\"]([A-Za-z0-9_.:-]{1,64})['\"]")
_CONTEXT_SUMMARY_PREFIX = "Prepare a short technical context for another model to answer the task."


def normalize_agent_route(route: Optional[str]) -> str:
    """Return a supported router contract name, defaulting to ordinary chat."""
    value = (route or "chat").strip()
    if value not in SUPPORTED_ROUTES:
        raise ValueError(f"Unsupported KITT agent route: {value!r}")
    return value


def infer_agent_route(
    system_prompt: Optional[str], messages: List[Dict[str, Any]]
) -> str:
    """Infer a contract route using KITT's existing TaskClassifier taxonomy.

    The inference happens before the reverse-proxy adapter strips the textual Tool
    Contract, so the route describes the actual tool surface being exposed for this
    turn rather than relying on a second proxy-specific classifier.
    """
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
    """Detach repository/project evidence from the system prompt.

    The returned first value contains orchestration/tool-contract text only. Repository
    evidence is returned separately so it can be sent as untrusted per-turn data rather
    than as a model system instruction.
    """
    if not system_prompt:
        return system_prompt, "not_provided"

    indexes = [
        (system_prompt.find(marker), marker)
        for marker in _PROJECT_CONTEXT_MARKERS
        if system_prompt.find(marker) >= 0
    ]
    if not indexes:
        return system_prompt, "not_provided"

    index, marker = min(indexes, key=lambda item: item[0])
    orchestration = system_prompt[:index].rstrip()
    workspace_text = system_prompt[index + len(marker) :].strip()
    workspace_context: Any = (
        {
            "trust": UNTRUSTED_WORKSPACE_LABEL,
            "source": "kitt-agent-cli",
            "data": workspace_text,
        }
        if workspace_text
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
