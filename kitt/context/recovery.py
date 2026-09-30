from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import Any

from kitt.context_filter.prompt_budget import TokenCounter
from kitt_protocol import ContextRecoveryRef, RecoveryMode


def recoverable_text_body(
    processor: Any,
    text: str,
    *,
    conversation_id: str,
    turn_id: str,
    artifact_type: str,
    summary: str,
    threshold_tokens: int = 1200,
    sensitivity: str = "NORMAL",
) -> dict[str, Any]:
    """Return inline text plus an exact recovery reference when it is large.

    The provider still receives the normal bounded text. The exact original is
    stored out-of-band before any later context-budget truncation, so compaction
    never becomes an irreversible information-loss operation.
    """
    value = str(text or "")
    body: dict[str, Any] = {"text": value}
    if not value or TokenCounter.count_tokens(value) < max(1, int(threshold_tokens)):
        return body

    store = getattr(processor, "artifact_store", None)
    workspace_id = str(getattr(processor, "workspace_id", "") or "")
    if store is None or not workspace_id:
        return body

    raw = value.encode("utf-8")
    try:
        artifact = store.put(
            workspace_id,
            raw,
            artifact_type,
            summary,
            conversation_id=conversation_id or None,
            turn_id=turn_id or None,
            sensitivity=sensitivity,
            metadata={
                "recovery": "EXACT",
                "source": "context-compiler",
            },
        )
    except Exception:
        return body

    ref = ContextRecoveryRef(
        artifact_id=artifact.id,
        sha256=hashlib.sha256(raw).hexdigest(),
        original_bytes=len(raw),
        token_estimate=TokenCounter.count_tokens(value),
        media_type="text/plain; charset=utf-8",
        recovery=RecoveryMode.EXACT,
    )
    body["recovery_ref"] = asdict(ref)
    return body
