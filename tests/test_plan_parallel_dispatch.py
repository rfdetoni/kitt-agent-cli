"""Release guard: one host plan action admits independent child workers safely."""
from types import SimpleNamespace

from kitt.runtime.core_runtime import CoreSafeRuntime
from kitt.runtime.safe_runtime import SafeRuntimeResult


class Plans:
    def __init__(self):
        self.tasks = [
            {"task_id": "one", "status": "PENDING", "paths": ["a.py"]},
            {"task_id": "two", "status": "PENDING", "paths": ["b.py"]},
            {"task_id": "overlap", "status": "PENDING", "paths": ["a.py"]},
            {"task_id": "dependent", "status": "PENDING", "paths": ["c.py"]},
        ]
        self.calls = []

    def next(self, conversation, turn):
        return {"plan": {"tasks": self.tasks}, "ready": ["one", "two", "overlap"]}

    def prepare_dispatch(self, conversation, turn, args, security_context):
        task = next(t for t in self.tasks if t["task_id"] == args["task_id"])
        return {"plan_task_id": task["task_id"], "allowed_paths": task["paths"]}


def _runtime(max_children=4, approved=True):
    plans = Plans()
    runtime = object.__new__(CoreSafeRuntime)
    runtime.registry = SimpleNamespace(task_plans=plans)
    runtime.children = SimpleNamespace(max_children=max_children)
    runtime.conversation_id = "conversation"
    submitted = []

    def spawn(operation, tool, payload, *other):
        assert tool == "child_spawn" and operation == "plan.dispatch_ready"
        submitted.append(payload["plan_task_id"])
        next(t for t in plans.tasks if t["task_id"] == payload["plan_task_id"])["status"] = "RUNNING"
        return SafeRuntimeResult(True, operation, context_handles=["child:" + payload["plan_task_id"]])

    if approved:
        runtime._op_registry_tool = spawn
    return runtime, plans, submitted


def test_parallel_ready_admits_two_disjoint_children_and_skips_overlap():
    runtime, plans, submitted = _runtime()
    result = runtime._op_plan(
        "plan.dispatch_ready", {}, "turn", "AGENT", SimpleNamespace(), None, None
    )
    assert result.success
    assert result.data["started"] == ["child:one", "child:two"]
    assert "overlap" in result.data["deferred"]
    assert submitted == ["one", "two"]
    assert plans.tasks[-1]["status"] == "PENDING"


def test_parallel_ready_obeys_requested_capacity():
    runtime, _, submitted = _runtime(max_children=2)
    result = runtime._op_plan(
        "plan.dispatch_ready", {"max_parallel": 1}, "turn", "AGENT", SimpleNamespace(), None, None
    )
    assert result.success
    assert submitted == ["one"]


def test_parallel_ready_stops_on_approval_without_implicit_grant():
    runtime, _, submitted = _runtime()
    def require_approval(operation, tool, payload, *other):
        submitted.append(payload["plan_task_id"])
        return SafeRuntimeResult(
            False, operation, requires_approval=True, approval_action="child_spawn",
            approval_payload=payload,
        )
    runtime._op_registry_tool = require_approval
    result = runtime._op_plan(
        "plan.dispatch_ready", {}, "turn", "AGENT", SimpleNamespace(), None, None
    )
    assert result.requires_approval
    assert submitted == ["one"]
