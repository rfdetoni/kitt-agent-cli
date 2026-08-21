from __future__ import annotations

from typing import Any

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


def _is_reasoning_event(event_type: str) -> bool:
    normalized = str(event_type or "").replace("_", "").replace("-", "").lower()
    return normalized.startswith("thinking") or normalized.startswith("reasoning")


def _bounded(value: Any, *, strip_reasoning: bool, depth: int = 0) -> Any:
    if depth >= _MAX_DEPTH:
        return "[truncated: max depth]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_STRING_CHARS else value[:_MAX_STRING_CHARS] + "…[truncated]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_ITEMS:
                result["_truncated_items"] = True
                break
            normalized_key = str(key).replace("-", "_").lower()
            if strip_reasoning and normalized_key in _REASONING_KEYS:
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
    text = str(value)
    return text if len(text) <= _MAX_STRING_CHARS else text[:_MAX_STRING_CHARS] + "…[truncated]"


def sanitize_public_event_payload(event_type: str, payload: Any) -> Any:
    """Return a bounded payload safe for daemon IPC, replay and Web exposure.

    Chain-of-thought-like keys are stripped only for public Thinking/Reasoning
    event families so legitimate fields with similar names in unrelated tool
    payloads are not silently destroyed.
    """

    return _bounded(payload, strip_reasoning=_is_reasoning_event(event_type))
