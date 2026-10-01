from __future__ import annotations

from types import SimpleNamespace

import pytest

from kitt.core.execution_budget import ExecutionBudgetExceeded, ExecutionBudgetLedger
from kitt.core.agent_runtime import reserve_child_budget, settle_child_budget
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


def test_provider_usage_reconciles_preflight_input_estimate():
    ledger = ExecutionBudgetLedger(_budget(max_input_tokens=100, max_total_tokens=160))
    ledger.reserve_model_call(input_tokens=60)
    ledger.reconcile_model_input(estimated_tokens=60, actual_tokens=25)
    ledger.record_model_output(output_tokens=20)

    snapshot = ledger.snapshot()
    assert snapshot["usage"]["input_tokens"] == 25
    assert snapshot["usage"]["output_tokens"] == 20
    assert snapshot["usage"]["total_tokens"] == 45


def test_provider_usage_reconciliation_still_enforces_hard_limit():
    ledger = ExecutionBudgetLedger(_budget(max_input_tokens=50, max_total_tokens=80))
    ledger.reserve_model_call(input_tokens=40)
    with pytest.raises(ExecutionBudgetExceeded):
        ledger.reconcile_model_input(estimated_tokens=40, actual_tokens=60)


def test_stage_usage_rolls_up_to_one_global_wallet():
    ledger = ExecutionBudgetLedger(
        _budget(max_model_calls=3, max_total_tokens=120, max_tool_calls=2)
    )
    ledger.reserve_model_call(input_tokens=20, stage="classifier")
    ledger.record_model_output(output_tokens=10, stage="classifier")
    ledger.reserve_model_call(input_tokens=30, stage="condenser")
    ledger.record_model_output(output_tokens=10, stage="condenser")
    ledger.reserve_tool_call(stage="validator")

    snapshot = ledger.snapshot()
    assert snapshot["usage"]["model_calls"] == 2
    assert snapshot["usage"]["total_tokens"] == 70
    assert snapshot["usage"]["tool_calls"] == 1
    assert snapshot["stages"]["classifier"]["input_tokens"] == 20
    assert snapshot["stages"]["classifier"]["output_tokens"] == 10
    assert snapshot["stages"]["condenser"]["input_tokens"] == 30
    assert snapshot["stages"]["validator"]["tool_calls"] == 1


def test_stage_names_do_not_create_independent_token_budgets():
    ledger = ExecutionBudgetLedger(_budget(max_total_tokens=50, max_input_tokens=50))
    ledger.reserve_model_call(input_tokens=30, stage="classifier")
    with pytest.raises(ExecutionBudgetExceeded):
        ledger.reserve_model_call(input_tokens=21, stage="execution")


def test_provider_usage_reconciles_preflight_input_cost():
    ledger = ExecutionBudgetLedger(_budget(max_cost=1.0))
    ledger.reserve_model_call(
        input_tokens=60,
        cost=0.30,
        stage="execution",
    )
    ledger.reconcile_model_input(
        estimated_tokens=60,
        actual_tokens=25,
        estimated_cost=0.30,
        actual_cost=0.10,
        stage="execution",
    )
    ledger.record_model_output(
        output_tokens=20,
        cost=0.20,
        stage="execution",
    )

    snapshot = ledger.snapshot()
    assert snapshot["usage"]["cost"] == pytest.approx(0.30)
    assert snapshot["stages"]["execution"]["cost"] == pytest.approx(0.30)


def test_provider_cost_reconciliation_enforces_hard_limit():
    ledger = ExecutionBudgetLedger(_budget(max_cost=0.25))
    ledger.reserve_model_call(input_tokens=10, cost=0.10)
    with pytest.raises(ExecutionBudgetExceeded):
        ledger.reconcile_model_input(
            estimated_tokens=10,
            actual_tokens=10,
            estimated_cost=0.10,
            actual_cost=0.30,
        )


def test_settled_child_usage_remains_in_parent_wallet():
    ledger = ExecutionBudgetLedger(
        _budget(
            max_model_calls=4,
            max_total_tokens=100,
            max_tool_calls=4,
            max_cost=1.0,
        )
    )
    lease = ledger.reserve_subagent(
        "child",
        token_cap=60,
        call_cap=2,
        cost_cap=0.5,
        tool_cap=2,
    )
    ledger.consume_child(
        lease.id,
        tokens=20,
        calls=1,
        cost=0.2,
        tools=1,
    )
    ledger.settle_child(lease.id)

    snapshot = ledger.snapshot()
    assert snapshot["usage"]["child_tokens"] == 20
    assert snapshot["usage"]["child_model_calls"] == 1
    assert snapshot["usage"]["child_tool_calls"] == 1
    assert snapshot["usage"]["child_cost"] == pytest.approx(0.2)
    assert snapshot["usage"]["total_tokens"] == 20
    assert snapshot["usage"]["model_calls"] == 1
    assert snapshot["usage"]["tool_calls"] == 1
    assert snapshot["usage"]["cost"] == pytest.approx(0.2)
    assert snapshot["reserved"] == {
        "tokens": 0,
        "calls": 0,
        "cost": 0.0,
        "tools": 0,
    }

    # Consumed child capacity remains spent after settlement.
    with pytest.raises(ExecutionBudgetExceeded):
        ledger.reserve_subagent(
            "too-large",
            token_cap=81,
            call_cap=1,
            cost_cap=0.0,
            tool_cap=1,
        )


def test_concurrent_child_tool_and_cost_leases_cannot_overspend_parent():
    ledger = ExecutionBudgetLedger(
        _budget(
            max_model_calls=4,
            max_total_tokens=100,
            max_tool_calls=3,
            max_cost=0.5,
        )
    )
    ledger.reserve_subagent(
        "first",
        token_cap=30,
        call_cap=1,
        cost_cap=0.3,
        tool_cap=2,
    )
    with pytest.raises(ExecutionBudgetExceeded):
        ledger.reserve_subagent(
            "second-tools",
            token_cap=10,
            call_cap=1,
            cost_cap=0.1,
            tool_cap=2,
        )
    with pytest.raises(ExecutionBudgetExceeded):
        ledger.reserve_subagent(
            "second-cost",
            token_cap=10,
            call_cap=1,
            cost_cap=0.3,
            tool_cap=1,
        )


def test_child_budget_controller_reserves_tools_cost_and_duration_then_rolls_usage_up():
    processor = SimpleNamespace(
        config=SimpleNamespace(
            max_model_calls_per_turn=8,
            max_input_tokens_per_turn=100,
            max_output_tokens_per_turn=100,
            max_total_tokens_per_turn=100,
            max_cost_per_turn=1.0,
            max_turn_duration_seconds=60.0,
            max_tool_calls_per_turn=6,
            max_subagents_per_turn=2,
        ),
        execution_budgets={},
        child_budget_leases={},
    )

    lease = reserve_child_budget(
        processor,
        "turn-parent",
        "child-1",
        40,
    )

    assert lease.token_cap == 40
    assert lease.call_cap > 0
    assert lease.cost_cap == pytest.approx(0.4)
    assert lease.reserved["tools"] > 0
    assert lease.reserved["duration_ms"] > 0

    settle_child_budget(
        processor,
        "child-1",
        tokens_used=12,
        calls_used=1,
        cost_used=0.1,
        tools_used=2,
    )

    assert "child-1" not in processor.child_budget_leases
    snapshot = processor.execution_budgets["turn-parent"].snapshot()
    assert snapshot["usage"]["child_tokens"] == 12
    assert snapshot["usage"]["child_model_calls"] == 1
    assert snapshot["usage"]["child_tool_calls"] == 2
    assert snapshot["usage"]["child_cost"] == pytest.approx(0.1)
