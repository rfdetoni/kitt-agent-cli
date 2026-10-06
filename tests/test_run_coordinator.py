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


def test_move_destination_is_fenced_against_other_turns(tmp_path):
    import pytest
    from kitt.native.coordinator import WorkspaceCoordinator, CoordinationConflict
    db = HistoryDatabase(str(tmp_path))
    repo = HistoryRepository(db)
    ws = repo.get_or_create_workspace(str(tmp_path))
    workspace = WorkspaceCoordinator(str(tmp_path), str(tmp_path), db, ws["id"])
    run = RunCoordinator(EventLedger(db), workspace_coordinator=workspace)
    run.claim_tool("conv", "turn", "kitt_runtime", {
        "operation": "repo.move", "arguments": {"path": "src/a.py", "target": "dst/b.py", "overwrite": True},
    })
    with pytest.raises(CoordinationConflict):
        workspace.claim_paths(["dst/b.py"], "other-turn", "write", wait_timeout=0)
    run.release_tool("conv", "turn")
    workspace.claim_paths(["dst/b.py"], "other-turn", "write", wait_timeout=0)
    workspace.release_owner("other-turn")
    db.close()


def test_recovery_reads_latest_state_after_more_than_one_page(tmp_path):
    db = HistoryDatabase(str(tmp_path))
    repo = HistoryRepository(db)
    ws = repo.get_or_create_workspace(str(tmp_path))
    conv = repo.create_conversation(ws["id"], "large run")
    ledger = EventLedger(db)
    coordinator = RunCoordinator(ledger)
    coordinator.transition(conv["id"], "turn", "RUNNING")
    for _ in range(10001):
        ledger.append_event(conv["id"], "Progress", {}, turn_id="turn")
    coordinator.transition(conv["id"], "turn", "IDLE")
    assert RunCoordinator(ledger).state(conv["id"], "turn").state == "IDLE"
    db.close()
