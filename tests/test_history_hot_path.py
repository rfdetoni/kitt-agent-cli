import sqlite3

from kitt.history.database import HistoryDatabase
from kitt.history.migrations import CURRENT_SCHEMA_VERSION, MigrationRunner
from kitt.history.repository import HistoryRepository


def _legacy_schema_connection(version: int) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE schema_info (version INTEGER PRIMARY KEY);
        CREATE TABLE session_events (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            turn_id TEXT,
            episode_id TEXT,
            sequence INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            model_visible INTEGER NOT NULL DEFAULT 0,
            replayable INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            UNIQUE(conversation_id, sequence)
        );
        CREATE TABLE child_sessions (
            id TEXT PRIMARY KEY,
            parent_conversation_id TEXT NOT NULL,
            parent_turn_id TEXT NOT NULL,
            name TEXT NOT NULL,
            task TEXT NOT NULL,
            state TEXT NOT NULL,
            depth INTEGER NOT NULL,
            model_profile TEXT NOT NULL,
            allowed_paths_json TEXT NOT NULL,
            enabled_tools_json TEXT NOT NULL,
            token_budget INTEGER NOT NULL,
            tokens_used INTEGER NOT NULL DEFAULT 0,
            timeout_seconds INTEGER NOT NULL,
            result_artifact_id TEXT,
            error TEXT,
            created_at REAL NOT NULL,
            started_at REAL,
            completed_at REAL,
            current_task_id TEXT,
            task_started_at REAL,
            capabilities_json TEXT DEFAULT '[]',
            context_summary TEXT DEFAULT '',
            runtime_conversation_id TEXT,
            security_context_json TEXT DEFAULT '{}'
        );
        CREATE TABLE remembered_approval_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_name TEXT NOT NULL,
            path_glob TEXT,
            decision TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        """
    )
    if version >= 8:
        conn.execute("ALTER TABLE session_events ADD COLUMN parent_event_id TEXT")
        conn.execute(
            "ALTER TABLE session_events ADD COLUMN source TEXT NOT NULL "
            "DEFAULT 'kitt-agent-cli'"
        )
        conn.execute(
            "ALTER TABLE session_events ADD COLUMN durability TEXT NOT NULL "
            "DEFAULT 'DURABLE'"
        )
    if version >= 9:
        conn.execute(
            "ALTER TABLE child_sessions ADD COLUMN budget_lease_json TEXT DEFAULT '{}'"
        )
        conn.execute(
            "ALTER TABLE child_sessions ADD COLUMN lineage_json TEXT DEFAULT '{}'"
        )
    conn.execute("INSERT INTO schema_info(version) VALUES (?)", (version,))
    conn.execute(
        """INSERT INTO session_events(
               id, conversation_id, sequence, event_type, payload_json,
               payload_hash, created_at
           ) VALUES ('event-1', 'conv-1', 1, 'test', '{}', 'hash', 1.0)"""
    )
    conn.execute(
        """INSERT INTO child_sessions(
               id, parent_conversation_id, parent_turn_id, name, task, state,
               depth, model_profile, allowed_paths_json, enabled_tools_json,
               token_budget, timeout_seconds, created_at
           ) VALUES (
               'child-1', 'conv-1', 'turn-1', 'child', 'task', 'READY',
               1, 'default', '[]', '[]', 100, 30, 1.0
           )"""
    )
    conn.execute(
        """INSERT INTO remembered_approval_rules(
               tool_name, path_glob, decision, created_at
           ) VALUES ('process.run', '*', 'allow', 1.0)"""
    )
    conn.commit()
    return conn


def test_schema_7_upgrades_to_current_without_discarding_history():
    conn = _legacy_schema_connection(7)
    MigrationRunner().migrate(conn)

    assert MigrationRunner().get_current_version(conn) == CURRENT_SCHEMA_VERSION
    event_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(session_events)")
    }
    child_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(child_sessions)")
    }
    approval_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(remembered_approval_rules)")
    }
    assert {"parent_event_id", "source", "durability"} <= event_columns
    assert {"budget_lease_json", "lineage_json"} <= child_columns
    assert {"workspace_id", "executable_identity"} <= approval_columns
    assert conn.execute(
        "SELECT COUNT(*) FROM session_events WHERE id='event-1'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM child_sessions WHERE id='child-1'"
    ).fetchone()[0] == 1
    # Legacy approvals were global and are intentionally invalidated at v10.
    assert conn.execute(
        "SELECT COUNT(*) FROM remembered_approval_rules"
    ).fetchone()[0] == 0


def test_schema_8_and_9_upgrade_idempotently_to_current():
    for version in (8, 9):
        conn = _legacy_schema_connection(version)
        MigrationRunner().migrate(conn)
        MigrationRunner().migrate(conn)
        assert MigrationRunner().get_current_version(conn) == CURRENT_SCHEMA_VERSION


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
