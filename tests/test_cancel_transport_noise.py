"""Operator transport cancellation should be terminal cancellation, not failure."""
from types import SimpleNamespace

from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import TurnCancelled, TurnFailed
from kitt.core.turn_processor import TurnProcessor
from kitt.llm.http_security import TransportCancelled


def test_operator_transport_cancel_has_no_traceback(caplog):
    p = object.__new__(TurnProcessor)
    p._attachment_paths_by_turn = {}
    p._attachment_wire_sent = set()
    p.registry = SimpleNamespace(queue_service=None)
    p._workspace_id = "ws"
    p.session_state = SimpleNamespace()
    p.cancelled_turns = set()
    p.event_callback = None

    def cancel(_cmd):
        raise TransportCancelled("Provider request cancelled")

    p._run_semantic_filter = cancel
    with caplog.at_level("ERROR"):
        events = list(p._run_turn_core(TurnCommand(conversation_id="c", prompt="test")))
    assert isinstance(events[-1], TurnCancelled)
    assert not any(isinstance(x, TurnFailed) for x in events)
    assert "Uncaught turn failure" not in caplog.text


def test_unrelated_provider_exception_still_fails():
    p = object.__new__(TurnProcessor)
    p._attachment_paths_by_turn = {}
    p._attachment_wire_sent = set()
    p.registry = SimpleNamespace(queue_service=None)
    p._workspace_id = "ws"
    p.session_state = SimpleNamespace()
    p.cancelled_turns = set()
    p.event_callback = None

    def fail(_cmd):
        raise RuntimeError("non-cancellation failure")

    p._run_semantic_filter = fail
    events = list(p._run_turn_core(TurnCommand(conversation_id="c", prompt="test")))
    assert isinstance(events[-1], TurnFailed)
