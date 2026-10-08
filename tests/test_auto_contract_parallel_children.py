"""Automatic contract parallelism: isolate independent work and reuse verified evidence."""
from types import SimpleNamespace

from kitt.goals.parallel_children import parallel_contract_result
from kitt.security.capabilities import CAP_CHILD_SPAWN


class State:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value, **kwargs):
        self.values[name] = value


class Repository:
    def __init__(self):
        self.children = {}

    def get(self, child_id):
        return self.children.get(child_id)


def _item(name, paths, dependencies=(), status="PENDING"):
    return SimpleNamespace(
        id=name, local_id=name, kind="task", status=status,
        depends_on=list(dependencies), paths=list(paths),
        prompt="Implement " + name, criteria=["Observable change"],
    )


def test_automatic_contract_fans_out_independent_children_without_reexecuting():
    first = _item("one", ["src/a.py"], status="RUNNING")
    second = _item("two", ["src/b.py"])
    dependent = _item("three", ["src/c.py"], ["one"])
    items = [first, second, dependent]
    repository = Repository()
    admissions = []

    def execute_tool(name, arguments, **kwargs):
        assert name == "child_spawn"
        assert arguments["enabled_tools"] == [
            "read_file", "search", "repository_map", "write_file", "apply_patch"
        ]
        assert kwargs["security_context"].principal_type == "GOAL"
        child_id = "child_" + arguments["name"]
        repository.children[child_id] = SimpleNamespace(
            state="COMPLETED", context_summary="Applied changes", tokens_used=150,
            error=None,
        )
        admissions.append((child_id, tuple(arguments["allowed_paths"])))
        return SimpleNamespace(
            success=True, requires_approval=False, error=None,
            metadata={"child_id": child_id},
        )

    runtime = SimpleNamespace(
        policy=SimpleNamespace(autonomy=SimpleNamespace(level="autonomous")),
        children=SimpleNamespace(max_children=4, repo=repository),
        goals=SimpleNamespace(
            contract_items=lambda goal_id: items,
            get=lambda goal_id: SimpleNamespace(state="RUNNING"),
        ),
        registry=SimpleNamespace(execute_tool=execute_tool),
        workspace_id="ws",
        config=SimpleNamespace(child_token_budget=2048, child_timeout_seconds=5),
    )
    state = State()
    goal = SimpleNamespace(id="goal", conversation_id="conversation")
    security = SimpleNamespace(principal_type="GOAL", capabilities={CAP_CHILD_SPAWN})

    first_result = parallel_contract_result(runtime, goal, first, state, security)
    assert first_result["status"] == "SUCCEEDED"
    assert first_result["paths"] == ["src/a.py"]
    assert len(admissions) == 2
    assert admissions[0][1] != admissions[1][1]

    # A later scheduler attempt verifies the already completed sibling.
    second.status = "RUNNING"
    second_result = parallel_contract_result(runtime, goal, second, state, security)
    assert second_result["status"] == "SUCCEEDED"
    assert second_result["paths"] == ["src/b.py"]
    assert len(admissions) == 2

    # Dependent work cannot start before the parent has been verified DONE.
    assert parallel_contract_result(runtime, goal, dependent, state, security) is None


def test_automatic_contract_does_not_parallelize_overlapping_paths():
    one = _item("one", ["src/shared.py"], status="RUNNING")
    two = _item("two", ["src/shared.py"])
    runtime = SimpleNamespace(
        policy=SimpleNamespace(autonomy=SimpleNamespace(level="autonomous")),
        goals=SimpleNamespace(contract_items=lambda goal_id: [one, two]),
        children=SimpleNamespace(max_children=4),
    )
    state = State()
    goal = SimpleNamespace(id="goal")
    security = SimpleNamespace(capabilities={CAP_CHILD_SPAWN})
    assert parallel_contract_result(runtime, goal, one, state, security) is None
