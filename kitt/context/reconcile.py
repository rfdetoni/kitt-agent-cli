from __future__ import annotations

import hashlib
import json
from typing import Any

from kitt_protocol import CacheRegion, SegmentDisposition


def _descriptor(segment) -> dict[str, Any]:
    return {
        "id": str(segment.id),
        "kind": str(segment.kind.value),
        "source": str(segment.source),
        "stability": str(segment.stability.value),
        "sensitivity": str(segment.sensitivity),
        "recovery": str(segment.recovery.value),
        "cache_region": str(segment.cache_region.value),
        "lifecycle": str(segment.lifecycle),
        "provenance_digest": str(segment.provenance_digest),
        "token_cost": int(segment.token_cost),
    }


def _logical_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("kind") or ""),
        str(item.get("source") or ""),
        str(item.get("lifecycle") or ""),
    )


def _previous_snapshot(ledger, conversation_id: str) -> dict[str, Any] | None:
    if ledger is None or not conversation_id:
        return None
    try:
        events = ledger.events(conversation_id, limit=10000)
    except Exception:
        return None
    for event in reversed(events):
        if event.event_type == "ContextEnvelopeSnapshot":
            return dict(event.payload)
    return None


def _cache_safe(profile: Any) -> bool:
    return bool(
        getattr(profile, "supports_prompt_cache", False)
        or getattr(profile, "prompt_cache_safe", False)
    )


def reconcile_context_envelope(
    processor: Any,
    envelope,
    *,
    conversation_id: str,
    turn_id: str,
    provider_profile: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reconcile a typed envelope against the last durable envelope snapshot."""
    ledger = getattr(processor, "event_ledger", None)
    previous = _previous_snapshot(ledger, conversation_id) or {}
    previous_segments = [
        dict(item)
        for item in previous.get("segments", [])
        if isinstance(item, dict)
    ]
    current_segments = [_descriptor(segment) for segment in envelope.segments]

    previous_by_id = {item["id"]: item for item in previous_segments if item.get("id")}
    previous_by_key = {_logical_key(item): item for item in previous_segments}
    current_ids = {item["id"] for item in current_segments}

    decisions: list[dict[str, Any]] = []
    for current in current_segments:
        old = previous_by_id.get(current["id"])
        if old is not None:
            disposition = (
                SegmentDisposition.UNCHANGED
                if old.get("provenance_digest") == current["provenance_digest"]
                else SegmentDisposition.RECONCILED
            )
            reason = "same segment identity" if disposition == SegmentDisposition.UNCHANGED else "segment content changed"
        else:
            logical = previous_by_key.get(_logical_key(current))
            disposition = SegmentDisposition.REPLACED
            reason = (
                f"replaces {logical.get('id')}"
                if logical is not None
                else "new logical segment"
            )
        decisions.append(
            {
                "segment_id": current["id"],
                "disposition": disposition.value,
                "reason": reason,
            }
        )

    for old in previous_segments:
        if old.get("id") and old["id"] not in current_ids:
            if _logical_key(old) not in {_logical_key(item) for item in current_segments}:
                decisions.append(
                    {
                        "segment_id": old["id"],
                        "disposition": SegmentDisposition.INVALIDATED.value,
                        "reason": "segment no longer present",
                    }
                )

    safe = _cache_safe(provider_profile)
    cache_regions: list[dict[str, Any]] = []
    if safe:
        allow_sensitive = bool(
            getattr(provider_profile, "cache_sensitive_context", False)
        )
        for region in (CacheRegion.FROZEN_PREFIX, CacheRegion.SESSION_PREFIX):
            members = [
                item
                for item in current_segments
                if item["cache_region"] == region.value
                and (
                    allow_sensitive
                    or item["sensitivity"].strip().lower()
                    in {"", "normal", "public"}
                )
            ]
            if not members:
                continue
            canonical = json.dumps(
                [
                    [item["id"], item["provenance_digest"]]
                    for item in members
                ],
                sort_keys=True,
                separators=(",", ":"),
            )
            cache_regions.append(
                {
                    "region": region.value,
                    "key": hashlib.sha256(
                        f"{envelope.epoch}|{region.value}|{canonical}".encode("utf-8")
                    ).hexdigest(),
                    "segment_ids": [item["id"] for item in members],
                    "token_cost": sum(item["token_cost"] for item in members),
                }
            )
    cache_plan = {
        "provider_cache_safe": safe,
        "regions": cache_regions,
        "observed_cached_tokens": None,
    }

    if ledger is not None and conversation_id:
        ledger.append_event(
            conversation_id,
            "ContextEnvelopeSnapshot",
            {
                "epoch": envelope.epoch,
                "segments": current_segments,
                "reconciliation": decisions,
                "cache_plan": cache_plan,
            },
            turn_id=turn_id,
            source="context-reconciler",
            durability="DURABLE",
            replayable=True,
        )
    return decisions, cache_plan


__all__ = ["reconcile_context_envelope"]
