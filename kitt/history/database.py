import os
import sqlite3
import hashlib
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 8


class _FileConnection(sqlite3.Connection):
    """A file-backed SQLite connection that closes when its context exits.

    ``sqlite3.Connection`` commits or rolls back in ``with`` blocks, but leaves
    the connection open.  Repositories intentionally use short-lived context
    blocks, so make that ownership explicit for file databases.
    """

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    canonical_path_hash TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    git_root TEXT,
    created_at REAL NOT NULL,
    last_opened_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    parent_conversation_id TEXT,
    forked_from_turn_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_turn_at REAL,
    model_context_profile TEXT,
    model_execution_profile TEXT,
    compact_summary TEXT,
    summary_version INTEGER DEFAULT 0,
    history_enabled INTEGER DEFAULT 1,
    metadata_json TEXT,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'CREATED',
    mode TEXT NOT NULL DEFAULT 'auto',
    user_message_id TEXT,
    assistant_message_id TEXT,
    semantic_intent TEXT,
    risk TEXT,
    confidence REAL,
    started_at REAL NOT NULL,
    completed_at REAL,
    error_code TEXT,
    changeset_id TEXT,
    parent_turn_id TEXT,
    is_compacted INTEGER DEFAULT 0,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    UNIQUE(conversation_id, ordinal)
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL,
    token_count INTEGER DEFAULT 0,
    token_count_method TEXT DEFAULT 'estimated',
    content_hash TEXT,
    is_compacted INTEGER DEFAULT 0,
    is_partial INTEGER DEFAULT 0,
    metadata_json TEXT,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY(turn_id) REFERENCES turns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    text TEXT NOT NULL,
    type TEXT NOT NULL,
    source_span TEXT,
    created_at REAL NOT NULL,
    active INTEGER DEFAULT 1,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS conversation_files (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    relation TEXT NOT NULL,
    file_hash TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pending_actions (
    id TEXT PRIMARY KEY,
    approval_request_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    normalized_args_json TEXT NOT NULL,
    action_hash TEXT NOT NULL,
    source_response_sha256 TEXT NOT NULL,
    affected_paths_json TEXT NOT NULL,
    before_hashes_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY(turn_id) REFERENCES turns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS telemetry_events (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    route TEXT NOT NULL,
    start_time REAL NOT NULL,
    duration_ms REAL NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    tokens_saved INTEGER DEFAULT 0,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY(turn_id) REFERENCES turns(id) ON DELETE CASCADE
);
"""

class HistoryDatabase:
    """SQLite database manager for persistent workspace conversation history."""

    def __init__(self, root_dir: str = ".", in_memory: bool = False):
        self.in_memory = in_memory or (root_dir == ":memory:")
        if self.in_memory:
            self.root_path = Path(".").resolve()
            self.db_path = ":memory:"
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
            self._mem_conn.execute("PRAGMA foreign_keys = ON;")
            self._init_memory_db()
        else:
            self.root_path = Path(root_dir).expanduser().resolve(strict=False)
            self.kitt_dir = self.root_path / ".kitt" / "history"
            self.kitt_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = self.kitt_dir / "history.sqlite3"
            self._init_db()

    def _init_memory_db(self):
        from kitt.history.migrations import MigrationRunner
        runner = MigrationRunner()
        runner.migrate(self._mem_conn)

    def get_connection(self) -> sqlite3.Connection:
        if self.in_memory:
            return self._mem_conn
        conn = sqlite3.connect(str(self.db_path), timeout=10.0, factory=_FileConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_db(self):
        setup_conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        try:
            setup_conn.execute("PRAGMA journal_mode = WAL;")
        finally:
            setup_conn.close()

        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        try:
            from kitt.history.migrations import MigrationRunner
            runner = MigrationRunner()
            runner.migrate(conn)
        finally:
            conn.close()

    def close(self) -> None:
        """Flush the WAL. Connections are short lived and owned by callers."""
        if self.in_memory:
            try:
                self._mem_conn.close()
            except Exception:
                pass
            return
        try:
            with self.get_connection() as conn:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
        except sqlite3.Error:
            pass
