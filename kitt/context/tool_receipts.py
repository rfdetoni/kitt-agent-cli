"""Context lifecycle helpers for consumed host-tool outputs."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from kitt.context_filter.prompt_budget import TokenCounter


_HOST_RESULT_MARKER = " result from the host."
_SUFFIX_MARKER = "\nIf the user's request is now satisfied"
_ARTIFACT_RE = re.compile(r"Artifact ID ([A-Za-z0-9_.:-]+)")


def _excerpt(value: str, max_chars: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max(0, max_chars - 3)].rstrip() + "..."


def compact_consumed_tool_results(
    messages: list[dict[str, Any]],
    *,
    min_tokens: int = 160,
    max_excerpt_chars: int = 320,
) -> int:
    """Replace consumed tool-result messages with deterministic receipts."""
    changed = 0
    threshold = max(1, int(min_tokens))
    excerpt_chars = max(32, int(max_excerpt_chars))
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        if content.startswith("[KITT TOOL RECEIPT]"):
            continue
        first_line, _, remainder = content.partition("\n")
        marker_at = first_line.casefold().find(_HOST_RESULT_MARKER)
        if marker_at <= 0 or TokenCounter.count_tokens(content) < threshold:
            continue
        tool_name = first_line[:marker_at].strip() or "host_tool"
        payload = remainder.split(_SUFFIX_MARKER, 1)[0].strip()
        digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        artifact = _ARTIFACT_RE.search(content)
        receipt = [
            "[KITT TOOL RECEIPT]",
            f"tool={tool_name}",
            "status=consumed",
            f"sha256={digest}",
            f"original_tokens={TokenCounter.count_tokens(content)}",
        ]
        if artifact:
            receipt.append(f"artifact_id={artifact.group(1)}")
        if payload:
            receipt.append(f"excerpt={_excerpt(payload, excerpt_chars)}")
        message["content"] = "\n".join(receipt)
        changed += 1
    return changed
