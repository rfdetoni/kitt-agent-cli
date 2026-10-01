"""Structural reverse-proxy agent contract identifiers.

Natural-language context, workspace data and tool authority are transported by
typed request fields. This module intentionally contains no prompt parsing or
prompt-envelope injection fallback.
"""
from __future__ import annotations

from typing import Optional


AGENT_CONTRACT_HEADER = "X-Kitt-Agent-Contract"
AGENT_CONTRACT_VERSION = "v2"
AGENT_ROUTE_HEADER = "X-Kitt-Route"

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


def normalize_agent_route(route: Optional[str]) -> str:
    """Return a supported structural router contract name."""
    value = (route or "chat").strip()
    if value not in SUPPORTED_ROUTES:
        raise ValueError(f"Unsupported KITT agent route: {value!r}")
    return value
