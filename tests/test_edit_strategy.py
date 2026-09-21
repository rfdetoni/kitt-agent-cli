from __future__ import annotations

from pathlib import Path

from kitt.domain.entities import SemanticTask
from kitt.edit_format.strategy import (
    EditStrategyContext,
    EditStrategyHistory,
    EditStrategySelector,
    EditStrategyTracker,
    infer_edit_strategy_context,
    edit_result_was_executed,
    strategy_for_tool_call,
)
from kitt.router.models import ModelCapabilities
from kitt.history.service import HistoryService


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
        original_prompt="refactor run",
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
    task = SemanticTask(original_prompt="corrija o bug", intent="DEBUG", paths=["service.py"])
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


def test_strong_model_with_multiple_existing_targets_prefers_unified_diff(tmp_path: Path):
    for name in ("one.py", "two.py"):
        (tmp_path / name).write_text("value = 1\n", encoding="utf-8")
    task = SemanticTask(
        original_prompt="fix both files",
        intent="DEBUG",
        paths=["one.py", "two.py"],
    )
    decision = EditStrategySelector().select(
        model_capabilities=_caps(),
        task=task,
        prompt="fix both files",
        explicit_files=("one.py", "two.py"),
        root_path=tmp_path,
        history={},
    )
    assert decision.strategy == "unified_diff"
    assert decision.scores["unified_diff"] > decision.scores["search_replace"]


def test_new_file_creation_prefers_whole_file(tmp_path: Path):
    task = SemanticTask(original_prompt="crie new_module.py", intent="IMPLEMENT", paths=["new_module.py"])
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
    task = SemanticTask(original_prompt="fix", intent="DEBUG", paths=["service.py"])
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
        "kitt_runtime",
        {
            "operation": "patch.apply",
            "arguments": {
                "patch": "service.py\n<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE"
            },
        },
    ) == "search_replace"
    assert strategy_for_tool_call(
        "kitt_runtime",
        {
            "operation": "patch.apply",
            "arguments": {
                "patch": "--- a/service.py\n+++ b/service.py\n@@ -1 +1 @@\n-old\n+new\n"
            },
        },
    ) == "unified_diff"
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



def test_feedback_metrics_are_conservative_and_track_costs():
    tracker = EditStrategyTracker()
    context = EditStrategyContext(
        workspace_id="ws",
        provider="openai",
        model="model",
        language="python",
        project_type="python",
    )
    for _ in range(3):
        tracker.record(
            "search_replace",
            False,
            context=context,
            failure_kind="apply_failure",
            output_tokens=100,
            latency_ms=20,
        )
    below_threshold = tracker.snapshot(context)["search_replace"]
    assert below_threshold.attempts == 3
    assert below_threshold.apply_failures == 3
    assert below_threshold.avg_output_tokens == 100
    assert below_threshold.avg_latency_ms == 20

    tracker.record(
        "search_replace",
        True,
        context=context,
        repair_required=True,
        rollback=True,
        files_changed=2,
        output_tokens=200,
        latency_ms=40,
    )
    stats = tracker.snapshot(context)["search_replace"]
    assert stats.attempts == 4
    assert stats.successes == 1
    assert stats.repair_rate == 0.25
    assert stats.rollback_rate == 0.25
    assert stats.files_changed == 2


def test_feedback_persists_by_workspace_model_language_and_project(tmp_path: Path):
    service = HistoryService(root_dir=str(tmp_path))
    try:
        context = EditStrategyContext(
            workspace_id=service.workspace_id,
            provider="openai",
            model="gpt-test",
            language="python",
            project_type="python",
        )
        writer = EditStrategyTracker(service.repo)
        writer.record(
            "structured_symbol",
            True,
            context=context,
            files_changed=1,
            output_tokens=80,
            latency_ms=12,
        )

        reader = EditStrategyTracker(service.repo)
        stats = reader.snapshot(context)["structured_symbol"]
        assert stats.attempts > 0.99
        assert stats.successes > 0.99
        assert stats.files_changed > 0.99

        other_model = EditStrategyContext(
            workspace_id=service.workspace_id,
            provider="openai",
            model="different-model",
            language="python",
            project_type="python",
        )
        assert reader.snapshot(other_model)["structured_symbol"].attempts == 0
    finally:
        service.close()


def test_context_inference_tracks_project_and_language(tmp_path: Path):
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    task = SemanticTask(
        original_prompt="refactor service",
        intent="REFACTOR",
        paths=["src/main/java/App.java"],
        technologies=["Spring Boot", "Java"],
    )
    context = infer_edit_strategy_context(
        workspace_id="ws",
        provider="openai",
        model="gpt-test",
        task=task,
        explicit_files=("src/main/java/App.java",),
        root_path=tmp_path,
    )
    assert context.language == "java"
    assert context.project_type == "java"
