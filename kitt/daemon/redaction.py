from __future__ import annotations

import re
from typing import Any

from kitt.history.redaction import redact as redact_history_secrets
from kitt.security.sensitive_data import SensitiveDataScanner

_MAX_DEPTH = 8
_MAX_ITEMS = 128
_MAX_STRING_CHARS = 64 * 1024
_REASONING_KEYS = frozenset(
    {
        "thought",
        "reasoning",
        "reasoning_content",
        "chain_of_thought",
        "chainofthought",
        "cot",
    }
)
# Exact semantic secret fields. Keep this exact rather than substring-based so
# aggregate telemetry such as tokens/input_tokens/max_tokens remains visible.
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "id_token",
        "auth_token",
        "authorization",
        "proxy_authorization",
        "password",
        "passwd",
        "secret",
        "client_secret",
        "private_key",
        "credential",
        "credentials",
        "cookie",
        "set_cookie",
        "nonce",
        "daemon_token",
        "session_token",
        "csrf_token",
        "pairing_token",
        "token",
    }
)


def _normalize_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key or "").strip().lower()).strip("_")


def _is_reasoning_event(event_type: str) -> bool:
    normalized = str(event_type or "").replace("_", "").replace("-", "").lower()
    return normalized.startswith("thinking") or normalized.startswith("reasoning")


_SERIALIZED_SECRET_PATTERNS = [
    # JSON string key-value: "api_key": "secret123"
    re.compile(r'(?i)(["\'](?:api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|id[_-]?token|auth[_-]?token|client[_-]?secret|password|passwd|private[_-]?key|pairing[_-]?token|daemon[_-]?token|session[_-]?token|csrf[_-]?token|secret)["\']\s*:\s*["\'])([^"\']+)(["\'])'),
    # Query params: ?api_key=secret123 or &token=secret123
    re.compile(r'(?i)([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|auth[_-]?token|secret)=)([^& \t\r\n\'"]+)'),
    # YAML / headers / env key-value: api_key: secret123 or API_KEY=secret123
    re.compile(r'(?i)(^[ \t]*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd|auth[_-]?token|private[_-]?key|secret)\s*[:=]\s*["\']?)([^ \t\r\n\'",;]+)(["\']?)', re.MULTILINE),
]


def _redact_text(value: str, *, depth: int = 0) -> str:
    """Apply both KITT secret redactors, JSON unpackers and pattern redactors before any public persistence/output."""
    if not value:
        return value
    if len(value) > _MAX_STRING_CHARS:
        value = value[:_MAX_STRING_CHARS] + "…[truncated]"

    stripped = value.strip()
    # Try parsing as JSON string if it appears to be a serialized JSON payload
    if depth < 4 and len(stripped) >= 2 and (
        (stripped.startswith("{") and stripped.endswith("}")) or
        (stripped.startswith("[") and stripped.endswith("]"))
    ):
        try:
            import json
            parsed = json.loads(stripped)
            sanitized = _bounded(parsed, strip_reasoning=False, depth=depth + 1)
            return json.dumps(sanitized, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            pass

    cleaned = SensitiveDataScanner.scan_and_redact(value).clean_text
    cleaned = redact_history_secrets(cleaned)

    for pattern in _SERIALIZED_SECRET_PATTERNS:
        if pattern.groups == 3:
            cleaned = pattern.sub(r"\1[REDACTED]\3", cleaned)
        elif pattern.groups == 2:
            cleaned = pattern.sub(r"\1[REDACTED]", cleaned)

    return cleaned


def _bounded(value: Any, *, strip_reasoning: bool, depth: int = 0) -> Any:
    if depth >= _MAX_DEPTH:
        return "[truncated: max depth]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_ITEMS:
                result["_truncated_items"] = True
                break
            normalized_key = _normalize_key(key)
            if strip_reasoning and normalized_key in _REASONING_KEYS:
                continue
            if normalized_key in _SECRET_KEYS:
                result[str(key)] = "[REDACTED_SECRET]"
                continue
            result[str(key)] = _bounded(
                item,
                strip_reasoning=strip_reasoning,
                depth=depth + 1,
            )
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        result = [
            _bounded(item, strip_reasoning=strip_reasoning, depth=depth + 1)
            for item in items[:_MAX_ITEMS]
        ]
        if len(items) > _MAX_ITEMS:
            result.append("[truncated: max items]")
        return result
    return _redact_text(str(value))


def sanitize_public_event_payload(event_type: str, payload: Any) -> Any:
    """Return bounded payload safe for daemon IPC, replay, persistence and Web.

    Reasoning fields are removed only for public Thinking/Reasoning families;
    exact secret fields and secret-looking string leaves are redacted for every
    event family before ``daemon_events`` persistence or SSE exposure.
    """
    return _bounded(payload, strip_reasoning=_is_reasoning_event(event_type))
