from __future__ import annotations

from types import SimpleNamespace

from kitt.goals.contract_execution import build_contract_prompt, prepare_contract_step
from kitt.goals.models import ContractItem, Goal


class _State:
    def get(self, key):
        return None

    def delete(self, key):
        raise AssertionError("No state should be deleted in this test")


def test_contract_step_keeps_original_objective_for_final_validation():
    objective = "Reverse engineer 2.1.308 and create pseudoC under reimplement/pseudoc."
    goal = Goal(
        id="goal_test", conversation_id="conversation_test", objective=objective,
        state="RUNNING", token_budget=None, max_turns=12, max_wall_seconds=600,
        tokens_used=0, turns_used=0, continuations_used=0,
        success_criteria=["original request"], capabilities=[],
    )
    item = ContractItem(
        id="item_test", goal_id=goal.id, position=0, local_id="T01",
        kind="task", title="Inspect ELF inventory",
        prompt="Inspect ELF headers and record the inventory only.",
        validation_prompt="Verify the inventory JSON exists.",
        criteria=["Inventory exists"], paths=["analysis/inventory.json"],
        check_ids=[], depends_on=[], status="RUNNING",
    )
    runtime = SimpleNamespace(goals=SimpleNamespace(
        current_item=lambda goal_id: item,
        contract_items=lambda goal_id: [item],
    ))

    step = prepare_contract_step(runtime, goal, _State())
    assert step.goal.objective == objective
    assert step.goal.success_criteria == item.criteria
    prompt = build_contract_prompt(runtime, goal, step, None)
    assert prompt.startswith(item.prompt)
    assert "reimplement/pseudoc" not in prompt.split("[KITT CONTRACT PROGRESS]")[0]
