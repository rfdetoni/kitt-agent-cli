"""Per-turn context envelope for the strict kitt-reverse-proxy agent contract."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


AGENT_CONTRACT_HEADER = "X-Kitt-Agent-Contract"
AGENT_CONTRACT_VERSION = "v1"
AGENT_ROUTE_HEADER = "X-Kitt-Route"
TURN_CONTEXT_MARKER = "[KITT TURN CONTEXT]"
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


def normalize_agent_route(route: Optional[str]) -> str:
    """Return a supported router contract name, defaulting to ordinary chat."""
    value = (route or "chat").strip()
    if value not in SUPPORTED_ROUTES:
        raise ValueError(f"Unsupported KITT agent route: {value!r}")
    return value


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
    payload = {
        "route": normalize_agent_route(route),
        "workspace_context": workspace_context,
    }
    context_message = {
        "role": "developer",
        "content": f"{TURN_CONTEXT_MARKER}\n{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}",
    }
    return [context_message, *[dict(message) for message in messages]]
