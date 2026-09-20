from __future__ import annotations

from pathlib import Path

from kitt.core.turn_architect import (
    ArchitectPlanner,
    parse_architect_handoff,
)
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_processor import TurnProcessor
from kitt.domain.entities import ModelProfile, SemanticTask
from kitt.router.models import TaskFeatures


def _features(**overrides):
    values = dict(
        intent="IMPLEMENT",
        secondary_intents=(),
        complexity="MEDIUM",
        risk="MEDIUM",
        requires_repository=True,
        requires_tools=True,
        requires_validation=True,
        estimated_files=1,
        cross_module=False,
        prompt_tokens=100,
        expected_context_tokens=1000,
        ambiguity=0.1,
        confidence=0.9,
        languages=("py",),
        paths=("a.py",),
        symbols=(),
        actions=("edit",),
        source="semantic",
    )
    values.update(overrides)
    return TaskFeatures(**values)


def test_architect_gate_only_runs_for_complex_mutation_with_profile():
    assert not ArchitectPlanner.should_use(
        _features(),
        mode="auto",
        enabled=True,
        profile_available=True,
    )
    assert ArchitectPlanner.should_use(
        _features(complexity="HIGH", estimated_files=4, cross_module=True),
        mode="auto",
        enabled=True,
        profile_available=True,
    )
    assert not ArchitectPlanner.should_use(
        _features(complexity="HIGH", estimated_files=4, cross_module=True),
        mode="plan",
        enabled=True,
        profile_available=True,
    )
    assert not ArchitectPlanner.should_use(
        _features(complexity="HIGH", estimated_files=4, cross_module=True),
        mode="auto",
        enabled=True,
        profile_available=False,
    )
    assert not ArchitectPlanner.should_use(
        _features(intent="ASK", complexity="HIGH", estimated_files=4),
        mode="auto",
        enabled=True,
        profile_available=True,
    )


def test_architect_handoff_parser_is_bounded_and_requires_objective_and_steps():
    raw = """```json
{"objective":"Refactor service","steps":["inspect","edit","test"],"files":["a.py"],"validation":["pytest"],"risks":["compat"]}
```"""
    handoff = parse_architect_handoff(raw, profile="architect-model")
    assert handoff is not None
    assert handoff.objective == "Refactor service"
    assert handoff.steps == ("inspect", "edit", "test")
    assert handoff.profile == "architect-model"
    rendered = handoff.render()
    assert "advisory" in rendered
    assert "pytest" in rendered
    assert parse_architect_handoff('{"objective":"x","steps":[]}') is None


def test_no_architect_profile_means_no_extra_model_call(tmp_path: Path, monkeypatch):
    processor = TurnProcessor(root_dir=str(tmp_path))
    task = SemanticTask(
        original_prompt="refatore vários módulos",
        intent="REFACTOR",
        paths=["a.py", "b.py", "c.py", "d.py"],
    )
    cmd = TurnCommand("conv", "refatore vários módulos", turn_id="turn-1")

    class ForbiddenClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("architect client must not be created without profile")

    monkeypatch.setattr("kitt.core.turn_architect.LLMClient", ForbiddenClient)
    try:
        assert processor._maybe_architect_handoff(cmd, task, "repo context", "") is None
    finally:
        processor.close()


def test_explicit_architect_profile_builds_separate_advisory_handoff(tmp_path: Path, monkeypatch):
    processor = TurnProcessor(root_dir=str(tmp_path))
    processor.router.config.profiles["architect"] = ModelProfile(
        backend="ollama",
        model="architect-model",
        context_window=8192,
        max_output_tokens=1200,
        supports_json=True,
    )
    task = SemanticTask(
        original_prompt="refatore vários módulos",
        intent="REFACTOR",
        paths=["a.py", "b.py", "c.py", "d.py"],
    )
    cmd = TurnCommand(
        "conv",
        "refatore vários módulos",
        turn_id="turn-architect",
    )
    calls = {}

    class FakeClient:
        def __init__(self, profile):
            calls["model"] = profile.model
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def chat(self, messages, **kwargs):
            calls["messages"] = messages
            calls.update(kwargs)
            return (
                '{"objective":"Refactor modules",'
                '"steps":["inspect dependencies","edit symbols","run tests"],'
                '"files":["a.py","b.py"],'
                '"validation":["pytest"],'
                '"risks":["API compatibility"]}'
            )

    monkeypatch.setattr("kitt.core.turn_architect.LLMClient", FakeClient)
    try:
        handoff = processor._maybe_architect_handoff(
            cmd,
            task,
            "Repository map evidence",
            "Explicit file evidence",
        )
        assert handoff is not None
        assert handoff.profile == "architect-model"
        assert calls["route"] == "context-gather"
        assert calls["session_key"] == "architect:conv:turn-architect"
        assert processor.session_state.architect_used is True
        assert processor.session_state.architect_profile == "architect-model"
        wire = calls["messages"][0]["content"]
        assert "<untrusted_workspace_context>" in wire
        assert "Repository map evidence" in wire
    finally:
        processor.close()
