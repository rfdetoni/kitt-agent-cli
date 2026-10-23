from types import SimpleNamespace

from kitt.context.reconcile import reconcile_context_envelope


def test_ephemeral_context_does_not_read_or_write_persistence_ledger():
    class Ledger:
        def events(self, *args, **kwargs):
            raise AssertionError("ephemeral reconciliation should not query history")
        def append_event(self, *args, **kwargs):
            raise AssertionError("ephemeral reconciliation should not persist")

    processor = SimpleNamespace(
        config=SimpleNamespace(history_enabled=False, persistence_enabled=False),
        event_ledger=Ledger(),
    )
    envelope = SimpleNamespace(segments=(), epoch=1)
    decisions, cache_plan = reconcile_context_envelope(
        processor, envelope, conversation_id="c", turn_id="t",
        provider_profile=SimpleNamespace(supports_prompt_cache=False),
    )
    assert decisions == []
    assert cache_plan["regions"] == []
