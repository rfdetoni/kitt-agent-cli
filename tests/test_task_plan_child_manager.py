from types import SimpleNamespace

from kitt.core.agent_runtime import install_agent_engineering
from kitt.tools.child_tools import ChildTools


class _EmptyLedger:
    def events(self, *_args, **_kwargs):
        return []


def test_task_plan_composition_uses_child_manager_repo(tmp_path):
    repo = SimpleNamespace(list=lambda *_args, **_kwargs: [])
    manager = SimpleNamespace(repo=repo)
    registry = SimpleNamespace(
        root_path=tmp_path,
        child_manager=manager,
        child_tools=ChildTools(manager),
        process_runner=None,
    )
    processor = SimpleNamespace(
        history_service=None,
        config=SimpleNamespace(max_correction_cycles=2),
        run_coordinator=None,
        registry=registry,
    )

    install_agent_engineering(processor, registry)

    assert processor.task_plans.children is manager
    processor.task_plans.ledger = _EmptyLedger()
    state = processor.task_plans.host_state("conversation-1", "turn-1")
    assert isinstance(state, dict)
