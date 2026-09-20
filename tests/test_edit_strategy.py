from __future__ import annotations

from pathlib import Path

from kitt.domain.entities import SemanticTask
from kitt.edit_format.strategy import (
    EditStrategyHistory,
    EditStrategySelector,
    EditStrategyTracker,
    edit_result_was_executed,
    strategy_for_tool_call,
)
from kitt.router.models import ModelCapabilities


def _caps(*, tier="large", local=False, edit=0.9, reasoning=0.9, context=32768):
    return ModelCapabilities(
        profile_name="test",
        tier=tier,
        input_context_limit=context,
        max_output_tokens=4096,
        supports_json=True,
        supports_native_tools=True,
        tool_call_reliability=0.9,
        code_edit_score=edit,
        reasoning_score=reasoning,
        languages=(),
        is_local=local,
        privacy_class="local" if local else "cloud",
    )


def test_strong_model_with_named_symbol_prefers_structured_edit(tmp_path: Path):
    target = tmp_path / "service.py"
    target.write_text("def run():\n    return 1\n", encoding="utf-8")
    task = SemanticTask(
        intent="REFACTOR",
        symbols=["run"],
        paths=["service.py"],
    )
    decision = EditStrategySelector().select(
        model_capabilities=_caps(),
        task=task,
        prompt="refactor run",
        explicit_files=("service.py",),
        root_path=tmp_path,
        history={},
    )
    assert decision.strategy == "structured_symbol"


def test_small_local_model_defaults_to_search_replace(tmp_path: Path):
    target = tmp_path / "service.py"
    target.write_text("x = 1\n", encoding="utf-8")
    task = SemanticTask(intent="DEBUG", paths=["service.py"])
    decision = EditStrategySelector().select(
        model_capabilities=_caps(
            tier="small",
            local=True,
            edit=0.7,
            reasoning=0.7,
            context=8192,
        ),
        task=task,
        prompt="corrija o bug",
        explicit_files=("service.py",),
        root_path=tmp_path,
        history={},
    )
    assert decision.strategy == "search_replace"


def test_new_file_creation_prefers_whole_file(tmp_path: Path):
    task = SemanticTask(intent="IMPLEMENT", paths=["new_module.py"])
    decision = EditStrategySelector().select(
        model_capabilities=_caps(tier="small", local=True, edit=0.75, reasoning=0.75, context=8192),
        task=task,
        prompt="crie new_module.py",
        explicit_files=("new_module.py",),
        root_path=tmp_path,
        history={},
    )
    assert decision.strategy == "whole_file"


def test_observed_history_changes_scores_without_single_attempt_flip(tmp_path: Path):
    task = SemanticTask(intent="DEBUG", paths=["service.py"])
    (tmp_path / "service.py").write_text("x=1\n", encoding="utf-8")
    tracker = EditStrategyTracker()
    tracker.record("search_replace", False)
    first = EditStrategySelector().select(
        model_capabilities=_caps(tier="small", local=True, edit=0.7, reasoning=0.7, context=8192),
        task=task,
        prompt="fix",
        explicit_files=("service.py",),
        root_path=tmp_path,
        history=tracker.snapshot(),
    )
    assert first.strategy == "search_replace"

    for _ in range(5):
        tracker.record("search_replace", False)
        tracker.record("structured_symbol", True)
    later = EditStrategySelector().select(
        model_capabilities=_caps(tier="small", local=True, edit=0.7, reasoning=0.7, context=8192),
        task=task,
        prompt="fix",
        explicit_files=("service.py",),
        root_path=tmp_path,
        history=tracker.snapshot(),
    )
    assert later.scores["structured_symbol"] > first.scores["structured_symbol"]
    assert later.scores["search_replace"] < first.scores["search_replace"]


def test_tool_call_mapping_and_nonexecution_filter():
    assert strategy_for_tool_call(
        "kitt_runtime", {"operation": "repo.edit_symbol"}
    ) == "structured_symbol"
    assert strategy_for_tool_call(
        "kitt_runtime", {"operation": "patch.apply"}
    ) == "search_replace"
    assert strategy_for_tool_call("write_file", {}) == "whole_file"

    class Result:
        success = False
        requires_approval = True
        error = "requires approval"

    assert edit_result_was_executed(Result()) is False

    class PolicyDenied:
        success = False
        requires_approval = False
        error = "Execution denied by PolicyEngine for tool 'write_file'."

    assert edit_result_was_executed(PolicyDenied()) is False
