"""Typed context IR and deterministic provider lowering.

This module is deliberately orchestration-only. Durable memory, repository state,
tools and provider sessions remain owned by their respective K.I.T.T. services.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from kitt.context_filter.prompt_budget import PromptBudget, TokenCounter
from kitt_protocol import (
    CacheRegion,
    ContextEnvelope,
    ContextKind,
    ContextSegment,
    ContextStability,
    ContextTrust,
    RecoveryMode,
)


_CACHE_ORDER = {
    CacheRegion.FROZEN_PREFIX: 0,
    CacheRegion.SESSION_PREFIX: 1,
    CacheRegion.LIVE_ZONE: 2,
    CacheRegion.UNCACHED: 3,
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _token_cost(value: Any) -> int:
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return TokenCounter.count_tokens(value["text"])
    return TokenCounter.count_tokens(_canonical(value))


def _truncate_body(body: Any, budget: int) -> Any:
    if budget <= 0:
        return {"text": ""}
    if not isinstance(body, dict) or not isinstance(body.get("text"), str):
        return body
    text = body["text"]
    if TokenCounter.count_tokens(text) <= budget:
        return body
    bounded = PromptBudget(8192, 1024)._truncate_to_tokens(text, budget)
    return {**body, "text": bounded, "truncated": True}


@dataclass
class ContextEnvelopeBuilder:
    epoch: str
    max_tokens: int | None = None

    def __post_init__(self) -> None:
        self._segments: list[ContextSegment] = []

    def add(
        self,
        kind: ContextKind,
        body_ref: Any,
        *,
        source: str,
        trust: ContextTrust,
        stability: ContextStability,
        priority: int,
        sensitivity: str = "normal",
        recovery: RecoveryMode = RecoveryMode.NONE,
        cache_region: CacheRegion = CacheRegion.LIVE_ZONE,
        lifecycle: str = "turn",
        ttl_turns: int | None = None,
    ) -> "ContextEnvelopeBuilder":
        if body_ref is None:
            return self
        if isinstance(body_ref, str):
            if not body_ref.strip():
                return self
            body_ref = {"text": body_ref}
        digest = _digest(body_ref)
        segment = ContextSegment(
            id=f"{kind.value.lower()}-{digest[:16]}",
            kind=kind,
            source=source,
            trust=trust,
            stability=stability,
            priority=int(priority),
            sensitivity=sensitivity,
            recovery=recovery,
            cache_region=cache_region,
            lifecycle=lifecycle,
            ttl_turns=ttl_turns,
            provenance_digest=digest,
            token_cost=_token_cost(body_ref),
            body_ref=body_ref,
        )
        self._segments.append(segment)
        return self

    def build(self) -> ContextEnvelope:
        segments = sorted(
            self._segments,
            key=lambda item: (_CACHE_ORDER[item.cache_region], -item.priority, item.id),
        )
        if self.max_tokens is not None:
            remaining = max(0, int(self.max_tokens))
            fitted: list[ContextSegment] = []
            for segment in segments:
                # User intent is carried by the user message and retained here for
                # provenance. It must never be dropped or rewritten for budget.
                if segment.kind == ContextKind.USER_INTENT:
                    fitted.append(segment)
                    continue
                if segment.token_cost <= remaining:
                    fitted.append(segment)
                    remaining -= segment.token_cost
                    continue
                if segment.priority >= 90 and remaining > 32:
                    body = _truncate_body(segment.body_ref, remaining)
                    fitted.append(
                        ContextSegment(
                            id=segment.id,
                            kind=segment.kind,
                            source=segment.source,
                            trust=segment.trust,
                            stability=segment.stability,
                            priority=segment.priority,
                            sensitivity=segment.sensitivity,
                            recovery=segment.recovery,
                            cache_region=segment.cache_region,
                            lifecycle=segment.lifecycle,
                            provenance_digest=segment.provenance_digest,
                            token_cost=_token_cost(body),
                            body_ref=body,
                            ttl_turns=segment.ttl_turns,
                        )
                    )
                    remaining = 0
            segments = fitted
        return ContextEnvelope(epoch=self.epoch, segments=tuple(segments))


def envelope_mapping(envelope: ContextEnvelope) -> dict[str, Any]:
    return envelope.to_mapping()


def _render_body(segment: ContextSegment) -> str:
    body = segment.body_ref
    if isinstance(body, dict):
        instructions = body.get("instructions")
        if isinstance(instructions, str) and instructions.strip():
            return instructions.strip()
        text = body.get("text")
        if isinstance(text, str):
            return text.strip()
    if isinstance(body, str):
        return body.strip()
    return _canonical(body)


def lower_context_envelope(envelope: ContextEnvelope) -> str:
    """Lower typed context for providers that only accept text.

    This is a one-way operation. No K.I.T.T. component is allowed to recover
    semantic categories by parsing the resulting labels.
    """
    parts: list[str] = []
    for segment in envelope.segments:
        if segment.kind == ContextKind.USER_INTENT:
            continue
        rendered = _render_body(segment)
        if not rendered:
            continue
        header = (
            f"[KITT_CONTEXT kind={segment.kind.value} trust={segment.trust.value} "
            f"stability={segment.stability.value} recovery={segment.recovery.value}]"
        )
        parts.append(f"{header}\n{rendered}\n[/KITT_CONTEXT]")
    return "\n\n".join(parts).strip()


def envelope_token_cost(envelope: ContextEnvelope) -> int:
    return sum(segment.token_cost for segment in envelope.segments)
