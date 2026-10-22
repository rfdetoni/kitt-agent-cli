"""Contract planning activity must be visible without claiming tool execution."""
from __future__ import annotations

import json
import asyncio
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kitt.core.turn_events import ContractProgress
from kitt.goals.contract import ContractPlanner
from kitt.ui.daemon_bridge import map_daemon_event_to_turn_event
from kitt.ui.reducer import reduce_ui_event
from kitt.ui.state import UIState


@pytest.mark.parametrize("daemon", [False, True])
def test_headless_cli_prints_planning_progress_before_execution(daemon):
    from kitt.core.turn_events import TurnCompleted
    from kitt.ui.fallback import HeadlessUI

    events = [ContractProgress(phase="generating", attempt=1, max_attempts=2),
              ContractProgress(phase="checking", attempt=1, max_attempts=2),
              TurnCompleted(response="PLAN_OK")]
    runtime = SimpleNamespace(
        config=SimpleNamespace(daemon_enabled=daemon, history_enabled=True, persistence_enabled=True),
        history=SimpleNamespace(get_or_create_active=lambda: {"id": "conv"}),
    )
    class Bridge:
        def __init__(self, runtime, callback, refresh):
            self.callback = callback
        async def ensure_daemon(self, conversation_id):
            return True
        async def start(self, *args, **kwargs):
            for event in events:
                self.callback(event)
        async def shutdown(self):
            pass
    output = StringIO()
    with patch("kitt.ui.event_bridge.TurnEventBridge", Bridge), patch(
        "kitt.goals.auto_contract.iter_automatic_contract", return_value=iter(events),
    ):
        assert asyncio.run(HeadlessUI(runtime, "Create the project", output).run_async()) == 0
    rendered = output.getvalue()
    assert "[contract] planning 1/2" in rendered
    assert "[contract] checking plan 1/2" in rendered
    assert rendered.index("planning") < rendered.index("PLAN_OK")
    assert "[Tool:" not in rendered


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


def test_local_structure_correction_streams_distinct_phases(tmp_path):
    planner = ContractPlanner(SimpleNamespace(canonical_root=tmp_path,workspace_id="workspace",processor=SimpleNamespace()))
    invalid = json.loads(_proposal());invalid["items"][0]["depends_on"]=["missing"]
    with patch.object(planner,"_run_plan_turn",side_effect=[json.dumps(invalid),_proposal()]) as run:
        events,items = _collect(planner.iter_plan("conv","Create a safe inventory"))
    assert [e.phase for e in events] == ["generating","checking","revising","generating","checking","accepted"]
    assert items[0]["local_id"] == "T00"
    assert run.call_count == 2


def test_invalid_plan_gets_one_correction_then_explicit_failure(tmp_path):
    planner = ContractPlanner(SimpleNamespace(canonical_root=tmp_path,workspace_id="workspace",processor=SimpleNamespace()))
    events = []
    with patch.object(planner,"_run_plan_turn",return_value='{"items":"invalid"}') as run:
        generator = planner.iter_plan("conv","Create a safe inventory")
        with pytest.raises(ValueError,match="failed after 2 attempts"):
            while True: events.append(next(generator))
    assert run.call_count == 2
    assert [e.attempt for e in events if e.phase=="revising"] == [1]
    assert events[-1].phase == "rejected"


def test_tui_shows_planning_and_review_without_false_completion():
    state = UIState()
    state.init_turn_tasks("Create observatory")
    reduce_ui_event(state, ContractProgress(phase="generating", attempt=1, max_attempts=3))
    assert state.status_text == "PLANNING 1/3"
    assert "ainda sem execução" in state.active_tasks[0].summary
    reduce_ui_event(state, ContractProgress(phase="checking", attempt=1, max_attempts=3))
    assert state.status_text == "PLAN CHECK 1/3"
    reduce_ui_event(state, ContractProgress(phase="revising", attempt=1, max_attempts=3, issues=4))
    assert "4 erro" in state.active_tasks[0].summary
    reduce_ui_event(state, ContractProgress(phase="executing", task_id="T00",
                                            task_state="RUNNING", completed=0, total=11))
    assert state.status_text == "GOAL EXECUTION"
    assert "0/11" in state.active_tasks[0].summary
    assert not any("concluído" in block.text.lower() for block in state.transcript)


def test_daemon_preserves_progress_fields_across_ipc():
    payload = {"phase": "checking", "attempt": 2, "max_attempts": 3, "issues": 1}
    wire = SimpleNamespace(event_type="ContractProgress", payload=payload)
    event = map_daemon_event_to_turn_event(wire)
    assert isinstance(event, ContractProgress)
    assert event.phase == "checking"
    assert event.attempt == 2
    assert event.max_attempts == 3


def test_structure_correction_carries_only_host_diagnostics_and_original_request(tmp_path):
    planner = ContractPlanner(SimpleNamespace(canonical_root=tmp_path,workspace_id="workspace",processor=SimpleNamespace()))
    invalid = json.loads(_proposal());invalid["items"][0]["depends_on"]=["missing"]
    with patch.object(planner,"_run_plan_turn",side_effect=[json.dumps(invalid),_proposal()]) as run:
        events,items = _collect(planner.iter_plan("conv","Build authorized inventory"))
    prompt = run.call_args_list[1].args[1]
    assert "Build authorized inventory" in prompt and "earlier ids" in prompt
    assert "Correct the reported host structural error only" in prompt
    assert events[-1].phase == "accepted" and len(items)==2


def test_malformed_correction_never_becomes_an_accepted_plan(tmp_path):
    planner = ContractPlanner(SimpleNamespace(canonical_root=tmp_path,workspace_id="workspace",processor=SimpleNamespace()))
    with patch.object(planner,"_run_plan_turn",side_effect=['{"items":[]}','{"items":"invalid"}']) as run:
        with pytest.raises(ValueError,match="failed after 2 attempts"):
            _collect(planner.iter_plan("conv","Build authorized inventory"))
    assert run.call_count == 2


def test_simulated_daemon_event_reaches_tui_before_real_tools():
    state = UIState()
    state.init_turn_tasks("Build Observatory")
    for phase, attempt in (("generating", 1), ("checking", 1),
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
        payload={"phase": "checking", "attempt": 2, "max_attempts": 3},
    )
    reduce_ui_event(state, map_daemon_event_to_turn_event(wire))
    ui = SimpleNamespace(state=state)
    assert "PLAN CHECK 2/3" in _status_text(ui)
    output = "".join(value for _, value in _transcript_text(ui))
    assert "Checagem local" in output
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
    reduce_ui_event(state, ContractProgress(phase="checking", attempt=1, max_attempts=3))
    assert state.transcript[-2].status == "done"
    assert state.transcript[-1].status == "running"
    assert "checagem local" in StatusBarComponent().render(state, width=125)
    assert "aguardando LLM" not in StatusBarComponent().render(state, width=125)
    reduce_ui_event(state, ContractProgress(phase="revising", attempt=1, max_attempts=3, issues=2))
    assert state.transcript[-2].status == "done"
    assert next(block for block in state.transcript if block.metadata.get("contract_phase")).status == "done"
    assert "2 erro" in state.active_tasks[0].summary


def test_thinking_events_do_not_clobber_contract_phase():
    from kitt.core.turn_events import ThinkingStarted, ThinkingCompleted, TurnFailed
    state = UIState()
    state.init_turn_tasks("Build observatory")
    reduce_ui_event(state, ContractProgress(phase="checking", attempt=2, max_attempts=3))
    reduce_ui_event(state, ThinkingStarted())
    reduce_ui_event(state, ThinkingCompleted(duration_ms=500))
    assert state.status_text == "PLAN CHECK 2/3"
    assert not any(block.kind == "thought" for block in state.transcript)
    reduce_ui_event(state, TurnFailed(error="ui_timeout"))
    assert state.contract_phase == ""
    assert next(block for block in state.transcript if block.metadata.get("contract_phase")).status == "done"


def test_high_risk_plan_uses_one_generation_and_local_acceptance(tmp_path):
    planner = ContractPlanner(SimpleNamespace(
        canonical_root=tmp_path, workspace_id='workspace', processor=SimpleNamespace(),
    ))
    proposal = _proposal().replace('README.md','security/policy.py')
    with patch.object(planner, '_run_plan_turn', side_effect=[proposal, AssertionError('unexpected plan review')]) as run:
        events, items = _collect(planner.iter_plan('conv','Harden security'))
    assert run.call_count == 1
    assert [event.phase for event in events] == ['generating','checking','accepted']
    assert items[0]['paths'] == ['security/policy.py']
