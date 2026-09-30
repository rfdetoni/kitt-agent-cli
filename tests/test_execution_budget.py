from __future__ import annotations

import pytest

from kitt.core.execution_budget import ExecutionBudgetExceeded, ExecutionBudgetLedger
from kitt_protocol import ExecutionBudget


def _budget(**overrides):
    data = {
        "max_model_calls": 4,
        "max_input_tokens": 100,
        "max_output_tokens": 100,
        "max_total_tokens": 160,
        "max_cost": 10.0,
        "max_duration_ms": 60_000,
        "max_tool_calls": 3,
        "max_subagents": 2,
    }
    data.update(overrides)
    return ExecutionBudget(**data)


def test_child_leases_reserve_parent_wallet_without_overspend():
    ledger = ExecutionBudgetLedger(_budget(max_total_tokens=100, max_model_calls=4))
    first = ledger.reserve_subagent("a", token_cap=60, call_cap=2)
    with pytest.raises(ExecutionBudgetExceeded):
        ledger.reserve_subagent("b", token_cap=50, call_cap=1)
    ledger.consume_child(first.id, tokens=20, calls=1)
    ledger.settle_child(first.id)
    second = ledger.reserve_subagent("b", token_cap=50, call_cap=1)
    assert second.child_agent_id == "b"


def test_model_and_tool_limits_are_hard():
    ledger = ExecutionBudgetLedger(_budget(max_model_calls=1, max_tool_calls=1))
    ledger.reserve_model_call(input_tokens=10)
    with pytest.raises(ExecutionBudgetExceeded):
        ledger.reserve_model_call(input_tokens=1)
    ledger.reserve_tool_call()
    with pytest.raises(ExecutionBudgetExceeded):
        ledger.reserve_tool_call()
