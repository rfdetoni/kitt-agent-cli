from __future__ import annotations

from kitt.core.run_coordinator import RunCoordinator
from kitt.evidence.ledger import EventLedger
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository


def test_run_state_is_persisted_before_projection(tmp_path):
    db = HistoryDatabase(str(tmp_path))
    repo = HistoryRepository(db)
    ws = repo.get_or_create_workspace(str(tmp_path))
    conv = repo.create_conversation(ws["id"], "run")
    ledger = EventLedger(db)
    coordinator = RunCoordinator(ledger)

    running = coordinator.observe_event(conv["id"], "turn-1", "TurnStarted")
    assert running.state == "RUNNING"
    failed = coordinator.observe_event(conv["id"], "turn-1", "TurnFailed")
    assert failed.state == "FAILED"

    events = ledger.events(conv["id"], turn_id="turn-1")
    assert [event.event_type for event in events] == [
        "RunStateChanged",
        "RunStateChanged",
    ]
    assert events[0].source == "run-coordinator"
    assert events[0].durability == "SYNC"
    db.close()
