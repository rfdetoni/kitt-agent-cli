"""Structural reverse-proxy routes and model-result decoding.

Natural-language context, workspace data and tool authority are transported by
typed request fields. This module intentionally contains no prompt parsing or
prompt-envelope injection fallback.
"""
from __future__ import annotations

from typing import Optional
import re
from kitt_protocol import decode_json_object



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


def parse_structured_result(response: str) -> dict:
    """Decode one complete model result, optionally protected by a JSON fence."""
    text = str(response or "").strip()
    fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n?```", text, re.IGNORECASE)
    return decode_json_object(fenced[1] if fenced else text)
