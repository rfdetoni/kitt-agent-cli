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
