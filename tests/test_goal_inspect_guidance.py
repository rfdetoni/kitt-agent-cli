"""Avoid repeated goal.inspect attempts for orchestrator-local task IDs."""
from types import SimpleNamespace

from kitt.runtime.core_runtime import SafeRuntime


def test_goal_inspect_does_not_confuse_local_task_with_persisted_goal():
    runtime = object.__new__(SafeRuntime)
    runtime.goals = SimpleNamespace(get_scoped=lambda goal_id, conversation_id: None)
    runtime.conversation_id = "conv"
    missing = runtime._op_goal_inspect({})
    assert not missing.success and "plan.inspect" in missing.error

    task = runtime._op_goal_inspect({"goal_id": "T01"})
    assert not task.success and "T01" in task.error and "plan.next" in task.error

    real_id = runtime._op_goal_inspect({"goal_id": "persisted-123"})
    assert not real_id.success and "persisted GoalService" in real_id.error
