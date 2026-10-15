"""Contract planning activity must be visible without claiming tool execution."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kitt.core.turn_events import ContractProgress
from kitt.goals.contract import ContractPlanner
from kitt.ui.daemon_bridge import map_daemon_event_to_turn_event
from kitt.ui.reducer import reduce_ui_event
from kitt.ui.state import UIState


def _proposal():
    def item(local_id, kind, paths, depends_on):
        return {
            "local_id": local_id, "kind": kind, "title": "Scoped work",
            "prompt": "Perform scoped work", "validation_prompt": "Verify output",
            "success_criteria": ["Actual validation evidence"],
            "check_ids": [], "paths": paths, "depends_on": depends_on,
        }
    return json.dumps({"items": [
        item("T00", "task", ["README.md"], []),
        item("FINAL", "final", [], ["T00"]),
    ]})


def _collect(iterator):
    events = []
    while True:
        try:
            events.append(next(iterator))
        except StopIteration as done:
            return events, done.value


def test_plan_review_revisions_stream_distinct_phases(tmp_path):
    planner = ContractPlanner(SimpleNamespace(
        canonical_root=tmp_path, workspace_id="workspace", processor=SimpleNamespace(),
    ))
    with patch.object(planner, "_run_plan_turn", return_value=_proposal()) as run, patch.object(
        planner, "_review_high_risk", side_effect=[["Fix the dependency"], []],
    ):
        events, items = _collect(planner.iter_plan("conv", "Create a safe inventory"))
    assert [event.phase for event in events] == [
        "generating", "reviewing", "revising",
        "generating", "reviewing", "accepted",
    ]
    assert [(event.attempt, event.issues) for event in events if event.phase == "revising"] == [(1, 1)]
    assert items[0]["local_id"] == "T00"
    assert run.call_count == 2


def test_three_revisions_have_explicit_terminal_failure(tmp_path):
    planner = ContractPlanner(SimpleNamespace(
        canonical_root=tmp_path, workspace_id="workspace", processor=SimpleNamespace(),
    ))
    events = []
    with patch.object(planner, "_run_plan_turn", return_value=_proposal()), patch.object(
        planner, "_review_high_risk", return_value=["Missing proof"],
    ):
        generator = planner.iter_plan("conv", "Create a safe inventory")
        with pytest.raises(ValueError, match="failed after 3 attempts"):
            while True:
                events.append(next(generator))
    assert [e.attempt for e in events if e.phase == "revising"] == [1, 2, 3]
    assert events[-1].phase == "rejected"


def test_tui_shows_planning_and_review_without_false_completion():
    state = UIState()
    state.init_turn_tasks("Create observatory")
    reduce_ui_event(state, ContractProgress(phase="generating", attempt=1, max_attempts=3))
    assert state.status_text == "PLANNING 1/3"
    assert "ainda sem execução" in state.active_tasks[0].summary
    reduce_ui_event(state, ContractProgress(phase="reviewing", attempt=1, max_attempts=3))
    assert state.status_text == "REVIEWING 1/3"
    reduce_ui_event(state, ContractProgress(phase="revising", attempt=1, max_attempts=3, issues=4))
    assert "4 questão" in state.active_tasks[0].summary
    reduce_ui_event(state, ContractProgress(phase="executing", task_id="T00",
                                            task_state="RUNNING", completed=0, total=11))
    assert state.status_text == "GOAL EXECUTION"
    assert "0/11" in state.active_tasks[0].summary
    assert not any("concluído" in block.text.lower() for block in state.transcript)


def test_daemon_preserves_progress_fields_across_ipc():
    payload = {"phase": "reviewing", "attempt": 2, "max_attempts": 3, "issues": 1}
    wire = SimpleNamespace(event_type="ContractProgress", payload=payload)
    event = map_daemon_event_to_turn_event(wire)
    assert isinstance(event, ContractProgress)
    assert event.phase == "reviewing"
    assert event.attempt == 2
    assert event.max_attempts == 3


def test_review_retry_edits_validated_plan_not_freeform_redecomposition(tmp_path):
    planner = ContractPlanner(SimpleNamespace(
        canonical_root=tmp_path, workspace_id="workspace", processor=SimpleNamespace(),
    ))
    with patch.object(
        planner, "_run_plan_turn", side_effect=[_proposal(), _proposal()],
    ) as run, patch.object(
        planner, "_review_high_risk", side_effect=[["T00 must wait for verified workspace"], []],
    ):
        events, items = _collect(planner.iter_plan("conv", "Build authorized inventory"))
    retry_prompt = run.call_args_list[1].args[1]
    assert "REVISION OF HOST-VALIDATED PLAN" in retry_prompt
    assert "T00 must wait for verified workspace" in retry_prompt
    assert '"local_id":"T00"' in retry_prompt
    assert "not a fresh decomposition" in retry_prompt
    assert [event.phase for event in events][-1] == "accepted"
    assert [item["local_id"] for item in items] == ["T00", "FINAL"]


def test_malformed_revision_retains_unresolved_review_issues(tmp_path):
    planner = ContractPlanner(SimpleNamespace(
        canonical_root=tmp_path, workspace_id="workspace", processor=SimpleNamespace(),
    ))
    with patch.object(
        planner, "_run_plan_turn",
        side_effect=[_proposal(), '{"items":"invalid"}', _proposal()],
    ) as run, patch.object(
        planner, "_review_high_risk", side_effect=[["Keep Windows execution gated"], []],
    ):
        events, items = _collect(planner.iter_plan("conv", "Build authorized inventory"))
    third_prompt = run.call_args_list[2].args[1]
    assert "Keep Windows execution gated" in third_prompt
    assert "Host validation:" in third_prompt
    assert [event.phase for event in events][-1] == "accepted"
    assert len(items) == 2


def test_simulated_daemon_event_reaches_tui_before_real_tools():
    state = UIState()
    state.init_turn_tasks("Build Observatory")
    for phase, attempt in (("generating", 1), ("reviewing", 1),
                           ("revising", 1), ("generating", 2)):
        wire = SimpleNamespace(
            event_type="ContractProgress",
            payload={"phase": phase, "attempt": attempt, "max_attempts": 3},
        )
        reduce_ui_event(state, map_daemon_event_to_turn_event(wire))
    assert state.status_text == "PLANNING 2/3"
    assert [block.kind for block in state.transcript] == ["system"] * 4
    assert not state.is_executing_tool
    wire = SimpleNamespace(event_type="ContractProgress",
                           payload={"phase": "executing", "task_id": "T00",
                                    "task_state": "RUNNING", "total": 11, "completed": 0})
    reduce_ui_event(state, map_daemon_event_to_turn_event(wire))
    assert "T00" in state.active_tasks[0].summary
    assert state.status_text == "GOAL EXECUTION"


def test_tui_render_contains_live_review_phase_in_hud_and_transcript():
    from kitt.ui.render.core import _status_text, _transcript_text

    state = UIState()
    state.init_turn_tasks("Build Observatory")
    wire = SimpleNamespace(
        event_type="ContractProgress",
        payload={"phase": "reviewing", "attempt": 2, "max_attempts": 3},
    )
    reduce_ui_event(state, map_daemon_event_to_turn_event(wire))
    ui = SimpleNamespace(state=state)
    assert "REVIEWING 2/3" in _status_text(ui)
    output = "".join(value for _, value in _transcript_text(ui))
    assert "Validando plano" in output
    assert "tentativa 2/3" in output
    assert "✔" not in output


def test_long_llm_wait_has_stage_and_real_elapsed_without_fake_progress(monkeypatch):
    from kitt.ui.render.core import _transcript_text, _status_text
    from kitt.ui.components.status_bar import StatusBarComponent
    import time

    state = UIState()
    state.init_turn_tasks("Build observatory")
    state.is_thinking = True
    reduce_ui_event(state, ContractProgress(phase="generating", attempt=1, max_attempts=3))
    state.contract_phase_started_at = time.time() - 71
    state.transcript[-1].started_at = time.time() - 71
    ui = SimpleNamespace(state=state)
    display = "".join(text for _, text in _transcript_text(ui))
    assert "71s" in display or "70s" in display
    assert "aguardando resposta do provedor" in display
    assert "PLANNING 1/3" in _status_text(ui)
    assert "aguardando LLM" in StatusBarComponent().render(state, width=125)
    reduce_ui_event(state, ContractProgress(phase="reviewing", attempt=1, max_attempts=3))
    assert state.transcript[-2].status == "done"
    assert state.transcript[-1].status == "running"
    assert "REVIEWING 1/3" in StatusBarComponent().render(state, width=125)
    reduce_ui_event(state, ContractProgress(phase="revising", attempt=1, max_attempts=3, issues=2))
    assert state.transcript[-2].status == "done"
    assert next(block for block in state.transcript if block.metadata.get("contract_phase")).status == "done"
    assert "2 ajuste" in state.active_tasks[0].summary


def test_thinking_events_do_not_clobber_contract_phase():
    from kitt.core.turn_events import ThinkingStarted, ThinkingCompleted, TurnFailed
    state = UIState()
    state.init_turn_tasks("Build observatory")
    reduce_ui_event(state, ContractProgress(phase="reviewing", attempt=2, max_attempts=3))
    reduce_ui_event(state, ThinkingStarted())
    reduce_ui_event(state, ThinkingCompleted(duration_ms=500))
    assert state.status_text == "REVIEWING 2/3"
    assert not any(block.kind == "thought" for block in state.transcript)
    reduce_ui_event(state, TurnFailed(error="ui_timeout"))
    assert state.contract_phase == ""
    assert state.transcript[-1].status == "done"
