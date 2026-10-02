from __future__ import annotations

import sys

from kitt.core.autonomy_policy import AutonomyPolicy
from kitt.evidence.ledger import EventLedger
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository
from kitt.tools.registry import ToolRegistry


def _ledger(tmp_path):
    db = HistoryDatabase(str(tmp_path))
    repo = HistoryRepository(db)
    ws = repo.get_or_create_workspace(str(tmp_path))
    conv = repo.create_conversation(ws["id"], "ledger")
    return db, EventLedger(db), conv["id"]


def test_event_is_persisted_before_publisher_observes_it(tmp_path):
    db, ledger, conversation_id = _ledger(tmp_path)
    observed = []

    def publish(kind, payload):
        rows = ledger.events(conversation_id)
        observed.append((kind, payload, [row.event_type for row in rows]))

    record = ledger.append_event(
        conversation_id,
        "Example",
        {"value": 1},
        turn_id="turn-1",
        event_id="event-stable",
        publisher=publish,
    )

    assert record.sequence == 1
    assert observed == [("Example", {"value": 1}, ["Example"])]
    db.close()


def test_reconnect_cursor_and_event_id_dedupe_do_not_duplicate_events(tmp_path):
    db, ledger, conversation_id = _ledger(tmp_path)
    first = ledger.append_event(
        conversation_id,
        "One",
        {"n": 1},
        turn_id="turn-1",
        event_id="event-one",
    )
    duplicate = ledger.append_event(
        conversation_id,
        "One",
        {"n": 1},
        turn_id="turn-1",
        event_id="event-one",
    )
    second = ledger.append_event(
        conversation_id,
        "Two",
        {"n": 2},
        turn_id="turn-1",
        event_id="event-two",
    )

    assert duplicate.id == first.id
    assert duplicate.sequence == first.sequence
    assert [row.id for row in ledger.events(conversation_id, after_sequence=first.sequence)] == [
        second.id
    ]
    assert [row.sequence for row in ledger.events(conversation_id)] == [1, 2]
    db.close()


def test_completed_mutation_execution_is_replayed_from_receipt_not_reserved_again(tmp_path):
    db, ledger, conversation_id = _ledger(tmp_path)
    registry = ToolRegistry(root_dir=str(tmp_path))
    registry.policy.autonomy = AutonomyPolicy.preset("autonomous")
    execution_id = "exec-write-1"
    args = {
        "argv": [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "p=Path('count.txt'); "
                "p.write_text((p.read_text() if p.exists() else '') + 'x')"
            ),
        ],
        "cwd": ".",
        "timeout_seconds": 30,
    }
    reserved = ledger.reserve_tool_execution(
        conversation_id,
        "turn-1",
        execution_id=execution_id,
        tool_call_id="call-1",
        tool_name="run_command",
        arguments_digest="digest",
        side_effecting=True,
    )
    assert reserved["state"] == "RESERVED"
    assert reserved["fresh"] is True
    assert reserved["operation_id"] == execution_id

    result = registry.execute_tool(
        "run_command",
        args,
        turn_id="turn-1",
        conversation_id=conversation_id,
        workspace_id="workspace-1",
        enabled_tools=["run_command"],
    )
    assert result.success, result.error
    assert (tmp_path / "count.txt").read_text(encoding="utf-8") == "x"

    completed = ledger.complete_tool_execution(
        conversation_id,
        "turn-1",
        execution_id=execution_id,
        attempt_id=reserved["attempt_id"],
        tool_call_id="call-1",
        tool_name="run_command",
        arguments_digest="digest",
        success=result.success,
        output=result.output,
        error=result.error,
        metadata=result.metadata,
    )
    assert completed.event_type == "ToolExecutionCompleted"

    replay = ledger.reserve_tool_execution(
        conversation_id,
        "turn-1",
        execution_id=execution_id,
        tool_call_id="call-1",
        tool_name="run_command",
        arguments_digest="digest",
        side_effecting=True,
    )
    assert replay["state"] == "COMPLETED"
    assert replay["outcome"] == "SUCCEEDED"
    assert replay["operation_id"] == execution_id
    assert replay["attempt_id"] == reserved["attempt_id"]
    assert "fresh" not in replay
    assert (tmp_path / "count.txt").read_text(encoding="utf-8") == "x"
    assert [row.event_type for row in ledger.events(conversation_id)] == [
        "ToolExecutionReserved",
        "ToolExecutionCompleted",
    ]
    registry.close()
    db.close()


def test_safe_retry_gets_new_attempt_id_without_changing_operation_id(tmp_path):
    db, ledger, conversation_id = _ledger(tmp_path)
    execution_id = "exec-read-1"
    first = ledger.reserve_tool_execution(
        conversation_id,
        "turn-1",
        execution_id=execution_id,
        tool_call_id="call-1",
        tool_name="read_file",
        arguments_digest="digest",
        side_effecting=False,
    )
    retry = ledger.reserve_tool_execution(
        conversation_id,
        "turn-1",
        execution_id=execution_id,
        tool_call_id="call-1",
        tool_name="read_file",
        arguments_digest="digest",
        side_effecting=False,
    )

    assert first["operation_id"] == retry["operation_id"] == execution_id
    assert first["attempt_id"] != retry["attempt_id"]
    assert retry["attempt_number"] == 2
    assert retry["outcome"] == "PENDING"
    assert [row.event_type for row in ledger.events(conversation_id)] == [
        "ToolExecutionReserved",
        "ToolExecutionRetry",
    ]

    recovered = ledger.tool_execution(execution_id)
    assert recovered is not None
    assert recovered["attempt_id"] == retry["attempt_id"]
    assert recovered["attempt_number"] == 2

    try:
        ledger.complete_tool_execution(
            conversation_id,
            "turn-1",
            execution_id=execution_id,
            attempt_id=first["attempt_id"],
            tool_call_id="call-1",
            tool_name="read_file",
            arguments_digest="digest",
            success=False,
            output="",
            error="stale attempt",
        )
    except ValueError as exc:
        assert "stale attempt_id" in str(exc)
    else:
        raise AssertionError("stale attempt completion must be rejected")

    completed = ledger.complete_tool_execution(
        conversation_id,
        "turn-1",
        execution_id=execution_id,
        attempt_id=retry["attempt_id"],
        tool_call_id="call-1",
        tool_name="read_file",
        arguments_digest="digest",
        success=False,
        output="",
        error="known failure",
    )
    state = ledger.tool_execution(execution_id)
    assert completed.payload["attempt_id"] == retry["attempt_id"]
    assert completed.payload["attempt_number"] == 2
    assert state is not None
    assert state["outcome"] == "FAILED"
    assert state["attempt_id"] == retry["attempt_id"]
    assert state["attempt_number"] == 2
    db.close()


def test_model_request_is_durable_without_eager_projection_checkpoints(tmp_path):
    db, ledger, conversation_id = _ledger(tmp_path)

    ledger.append_model_request(
        conversation_id,
        "turn-1",
        system_prompt="system",
        messages=[{"role": "user", "content": "hello"}],
        route="agent-loop",
        profile="kitt-reverse-proxy",
        model="gemini-web",
    )

    latest = ledger.latest_model_request(conversation_id, "turn-1")
    assert latest is not None
    assert latest["route"] == "agent-loop"
    assert latest["model"] == "gemini-web"

    with db.get_connection() as conn:
        checkpoints = conn.execute(
            "SELECT projection_key FROM session_projection_cache WHERE conversation_id=?",
            (conversation_id,),
        ).fetchall()
    assert checkpoints == []
    db.close()


def test_periodic_projection_checkpoint_uses_one_write_transaction(tmp_path, monkeypatch):
    db, ledger, conversation_id = _ledger(tmp_path)
    for index in range(7):
        ledger.append_event(
            conversation_id,
            "Example",
            {"index": index},
            turn_id="turn-1",
        )

    original_get_connection = db.get_connection
    connection_calls = 0

    def counted_get_connection():
        nonlocal connection_calls
        connection_calls += 1
        return original_get_connection()

    monkeypatch.setattr(db, "get_connection", counted_get_connection)
    ledger.append_event(
        conversation_id,
        "Example",
        {"index": 7},
        turn_id="turn-1",
    )

    # One transaction persists the event and one batches all projection checkpoints.
    assert connection_calls == 2
    db.close()
