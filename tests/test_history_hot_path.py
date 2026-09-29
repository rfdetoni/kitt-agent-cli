import sqlite3

from kitt.history.database import HistoryDatabase
from kitt.history.migrations import (
    IncompatibleSchemaError,
    MigrationRunner,
    SCHEMA_V1_STATEMENTS,
)
from kitt.history.repository import HistoryRepository


def test_obsolete_schema_is_rejected_instead_of_upgraded():
    conn = sqlite3.connect(":memory:")
    for statement in SCHEMA_V1_STATEMENTS:
        conn.execute(statement)
    conn.execute("INSERT INTO schema_info (version) VALUES (1)")
    conn.commit()

    try:
        MigrationRunner().migrate(conn)
    except IncompatibleSchemaError:
        pass
    else:
        raise AssertionError("obsolete Agent state must require explicit reset")


def test_tool_gain_prefix_range_keeps_telemetry_semantics():
    db = HistoryDatabase(":memory:", in_memory=True)
    repo = HistoryRepository(db)
    try:
        workspace = repo.get_or_create_workspace("telemetry-range-workspace")
        conversation = repo.create_conversation(workspace["id"], title="Telemetry")
        conv_id = conversation["id"]
        repo.save_message(conv_id, "turn-1", "user", "hello")

        repo.save_tool_gain(
            conv_id,
            "turn-1",
            "repo.read",
            1.0,
            2.0,
            100,
            25,
            75,
        )
        repo.save_telemetry(
            conv_id,
            "turn-1",
            "tool_gain;not-a-prefix-match",
            2.0,
            3.0,
            1,
            1,
            0,
        )
        repo.save_telemetry(
            conv_id,
            "turn-1",
            "model",
            3.0,
            4.0,
            2,
            2,
            0,
        )

        gain = repo.get_gain_summary(conv_id)
        regular = repo.get_telemetry_stats(conv_id)

        assert gain["count"] == 1
        assert gain["saved"] == 75
        assert regular["count"] == 2
    finally:
        db.close()


def test_fork_batches_conversation_update_and_preserves_message_order():
    db = HistoryDatabase(":memory:", in_memory=True)
    repo = HistoryRepository(db)
    try:
        workspace = repo.get_or_create_workspace("fork-batch-workspace")
        conversation = repo.create_conversation(workspace["id"], title="Original")
        for index in range(6):
            repo.save_message(
                conversation["id"],
                f"turn-{index}",
                "user",
                f"message-{index}",
            )

        statements: list[str] = []
        db._mem_conn.set_trace_callback(statements.append)
        forked = repo.fork_conversation(conversation["id"])
        db._mem_conn.set_trace_callback(None)

        updates = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith(
                "UPDATE CONVERSATIONS SET UPDATED_AT"
            )
        ]
        messages = repo.get_messages_for_conversation(forked["id"])

        assert len(updates) == 1
        assert [message["content"] for message in messages] == [
            f"message-{index}" for index in range(6)
        ]
    finally:
        db.close()


def test_message_order_uses_turn_ordinal_when_timestamps_tie():
    db = HistoryDatabase(":memory:", in_memory=True)
    repo = HistoryRepository(db)
    try:
        workspace = repo.get_or_create_workspace("stable-order-workspace")
        conversation = repo.create_conversation(workspace["id"], title="Stable")
        conv_id = conversation["id"]

        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO turns (id, conversation_id, ordinal, started_at) VALUES (?, ?, ?, ?)",
                ("turn-1", conv_id, 1, 1.0),
            )
            conn.execute(
                "INSERT INTO turns (id, conversation_id, ordinal, started_at) VALUES (?, ?, ?, ?)",
                ("turn-2", conv_id, 2, 1.0),
            )
            conn.execute(
                """INSERT INTO messages
                   (id, conversation_id, turn_id, role, content, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("z-message", conv_id, "turn-1", "user", "first", 10.0),
            )
            conn.execute(
                """INSERT INTO messages
                   (id, conversation_id, turn_id, role, content, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("a-message", conv_id, "turn-2", "user", "second", 10.0),
            )

        messages = repo.get_messages_for_conversation(conv_id)
        assert [message["content"] for message in messages] == ["first", "second"]
    finally:
        db.close()
