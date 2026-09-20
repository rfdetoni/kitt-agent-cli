from __future__ import annotations

import re
from typing import Optional


_CHAT_LIMIT_PATTERNS = [
    re.compile(
        r"\b(?:you(?:'ve|\s+have)\s+reached\s+(?:your\s+|the\s+)?(?:current\s+)?(?:message|usage|rate|hourly|daily|turn|request|\s+)*limit"
        r"|message\s+limit\s+(?:reached|exceeded)"
        r"|usage\s+limit\s+(?:reached|exceeded)"
        r"|rate\s+limit\s+(?:reached|exceeded)"
        r"|too\s+many\s+requests(?:,\s*please\s*try\s*again)?"
        r"|try\s+again\s+(?:in|after)\s+\d+\s+(?:minute|hour|second)s?"
        r"|session\s+limit\s+exceeded"
        r"|quota\s+limit\s+exceeded)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:você\s+atingiu\s+(?:o\s+|seu\s+)?limite(?:\s+de\s+(?:mensagens|uso|requisições|taxa))?"
        r"|limite\s+(?:de\s+mensagens|de\s+uso|de\s+taxa|por\s+hora|diário)?\s*(?:atingido|excedido)"
        r"|muitas\s+requisições,\s*tente\s+novamente"
        r"|tente\s+novamente\s+(?:em|mais\s+tarde|após)\s*(?:\d+\s*(?:minuto|hora|segundo)s?)?)\b",
        re.IGNORECASE,
    ),
]


def detect_chat_limit_message(text: str) -> Optional[str]:
    """Detect upstream chat/provider message and rate limit announcements in model text."""
    if not text:
        return None
    for pattern in _CHAT_LIMIT_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(0)
    return None


def _reverse_proxy_identity(profile) -> Optional[tuple[str, str]]:
    backend = str(getattr(profile, "backend", "") or "").strip().lower()
    protocol = str(getattr(profile, "protocol", "") or "").strip().lower()
    if backend not in {"kitt-reverse-proxy", "kitt-proxy"} and protocol != "kitt-reverse-proxy":
        return None
    base_url = str(getattr(profile, "base_url", "") or "http://127.0.0.1:3000").strip().rstrip("/")
    credential = str(getattr(profile, "credential_ref", "") or getattr(profile, "api_key", "") or "")
    return base_url, credential


def _same_reverse_proxy_endpoint(left, right) -> bool:
    left_identity = _reverse_proxy_identity(left)
    return left_identity is not None and left_identity == _reverse_proxy_identity(right)
