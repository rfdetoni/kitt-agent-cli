from kitt.core.turn_processor import TurnProcessor
from kitt.context.token_ledger import TokenLedger


def test_token_ledger_reuses_unchanged_messages():
    ledger = TokenLedger()
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]

    first = ledger.total_input_tokens("system", messages)
    first_snapshot = ledger.snapshot()
    second = ledger.total_input_tokens("system", messages)
    second_snapshot = ledger.snapshot()

    assert first == second
    assert first_snapshot["recomputed_entries"] == 2
    assert second_snapshot["reused_entries"] >= 2


def test_token_ledger_recomputes_only_mutated_message():
    ledger = TokenLedger()
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    ledger.total_input_tokens("system", messages)
    before = ledger.snapshot()["recomputed_entries"]

    messages[1]["content"] = "world changed substantially"
    ledger.total_input_tokens("system", messages)
    after = ledger.snapshot()

    assert after["recomputed_entries"] == before + 1
    assert after["reused_entries"] >= 1


def test_turn_processor_lazily_provisions_ledger_for_partial_harness():
    processor = object.__new__(TurnProcessor)

    ledger = processor._token_ledger_instance()

    assert ledger is processor._token_ledger
    assert ledger.total_input_tokens("system", [{"role": "user", "content": "hello"}]) > 0
