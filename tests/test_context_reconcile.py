from types import SimpleNamespace

from kitt.context.reconcile import reconcile_context_envelope
from kitt_protocol import (
    CacheRegion,
    ContextKind,
    ContextStability,
    ContextTrust,
    RecoveryMode,
)


class Ledger:
    def __init__(self):
        self.rows = []

    def events(self, conversation_id, limit=10000):
        del conversation_id, limit
        return list(self.rows)

    def append_event(self, conversation_id, event_type, payload, **kwargs):
        row = SimpleNamespace(
            conversation_id=conversation_id,
            event_type=event_type,
            payload=dict(payload),
            turn_id=kwargs.get("turn_id"),
        )
        self.rows.append(row)
        return row


def _segment(
    segment_id,
    digest,
    *,
    source="repo",
    kind=ContextKind.REPOSITORY_EVIDENCE,
    region=CacheRegion.LIVE_ZONE,
    sensitivity="private",
):
    return SimpleNamespace(
        id=segment_id,
        kind=kind,
        source=source,
        stability=ContextStability.TURN,
        sensitivity=sensitivity,
        recovery=RecoveryMode.RECOMPUTE,
        cache_region=region,
        lifecycle="turn",
        provenance_digest=digest,
        token_cost=10,
    )


def test_reconcile_classifies_unchanged_reconciled_replaced_and_invalidated():
    ledger = Ledger()
    processor = SimpleNamespace(event_ledger=ledger)
    profile = SimpleNamespace(supports_prompt_cache=False)

    first = SimpleNamespace(
        epoch="epoch-1",
        segments=[
            _segment("same", "digest-a", source="same-source"),
            _segment("changed", "digest-old", source="changed-source"),
            _segment("replaced-old", "digest-old", source="logical-source"),
            _segment("removed", "digest-old", source="removed-source"),
        ],
    )
    reconcile_context_envelope(
        processor,
        first,
        conversation_id="conv",
        turn_id="turn-1",
        provider_profile=profile,
    )

    second = SimpleNamespace(
        epoch="epoch-2",
        segments=[
            _segment("same", "digest-a", source="same-source"),
            _segment("changed", "digest-new", source="changed-source"),
            _segment("replaced-new", "digest-new", source="logical-source"),
        ],
    )
    decisions, cache_plan = reconcile_context_envelope(
        processor,
        second,
        conversation_id="conv",
        turn_id="turn-2",
        provider_profile=profile,
    )

    by_id = {item["segment_id"]: item["disposition"] for item in decisions}
    assert by_id["same"] == "UNCHANGED"
    assert by_id["changed"] == "RECONCILED"
    assert by_id["replaced-new"] == "REPLACED"
    assert by_id["removed"] == "INVALIDATED"
    assert cache_plan == {
        "provider_cache_safe": False,
        "regions": [],
        "observed_cached_tokens": None,
    }


def test_cache_plan_excludes_sensitive_segments_unless_provider_explicitly_allows_them():
    ledger = Ledger()
    processor = SimpleNamespace(event_ledger=ledger)
    envelope = SimpleNamespace(
        epoch="epoch-cache",
        segments=[
            _segment(
                "public",
                "digest-public",
                source="system",
                kind=ContextKind.SYSTEM_INSTRUCTION,
                region=CacheRegion.FROZEN_PREFIX,
                sensitivity="public",
            ),
            _segment(
                "secret",
                "digest-secret",
                source="secret",
                kind=ContextKind.MEMORY_RECALL,
                region=CacheRegion.FROZEN_PREFIX,
                sensitivity="secret",
            ),
        ],
    )

    _, safe_plan = reconcile_context_envelope(
        processor,
        envelope,
        conversation_id="conv",
        turn_id="turn-1",
        provider_profile=SimpleNamespace(
            supports_prompt_cache=True,
            cache_sensitive_context=False,
        ),
    )
    assert safe_plan["provider_cache_safe"] is True
    assert safe_plan["regions"][0]["segment_ids"] == ["public"]

    ledger.rows.clear()
    _, sensitive_plan = reconcile_context_envelope(
        processor,
        envelope,
        conversation_id="conv",
        turn_id="turn-2",
        provider_profile=SimpleNamespace(
            supports_prompt_cache=True,
            cache_sensitive_context=True,
        ),
    )
    assert sensitive_plan["regions"][0]["segment_ids"] == ["public", "secret"]
