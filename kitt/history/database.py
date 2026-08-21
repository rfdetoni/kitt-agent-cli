import os
import sqlite3
import hashlib
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 16


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

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    normalized_content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_accessed_at REAL,
    access_count INTEGER NOT NULL DEFAULT 0,
    valid_from REAL,
    valid_until REAL,
    supersedes_id TEXT,
    content_hash TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT DEFAULT '{}',
    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memories_ws_status
ON memories(workspace_id, status);

CREATE INDEX IF NOT EXISTS idx_memories_content_hash
ON memories(workspace_id, content_hash);

CREATE TABLE IF NOT EXISTS memory_evidence (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    session_entry_id TEXT,
    conversation_id TEXT,
    source_kind TEXT NOT NULL,
    evidence_text TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_evidence_memory_id
ON memory_evidence(memory_id);

CREATE INDEX IF NOT EXISTS idx_evidence_session_entry
ON memory_evidence(session_entry_id);

CREATE TABLE IF NOT EXISTS dream_runs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL,
    sessions_scanned INTEGER NOT NULL DEFAULT 0,
    entries_scanned INTEGER NOT NULL DEFAULT 0,
    signals_found INTEGER NOT NULL DEFAULT 0,
    memories_added INTEGER NOT NULL DEFAULT 0,
    memories_merged INTEGER NOT NULL DEFAULT 0,
    memories_superseded INTEGER NOT NULL DEFAULT 0,
    memories_archived INTEGER NOT NULL DEFAULT 0,
    model TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    failure_reason TEXT,
    dry_run INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_dream_runs_ws_started
ON dream_runs(workspace_id, started_at);
"""

import threading

class _InMemoryConnectionContext:
    """Context that serializes transactions with RLock and commits/rollbacks without closing shared in-memory connection."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        self._conn = conn
        self._lock = lock

    def __enter__(self) -> sqlite3.Connection:
        self._lock.acquire()
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
            else:
                try:
                    self._conn.commit()
                except Exception:
                    pass
        finally:
            self._lock.release()
        return False

    def __getattr__(self, name):
        return getattr(self._conn, name)


class HistoryDatabase:
    """SQLite database manager for persistent workspace conversation history."""

    def __init__(self, root_dir: str = ".", in_memory: bool = False):
        self.in_memory = in_memory or (root_dir == ":memory:")
        self._mem_lock = threading.RLock()
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
        with self._mem_lock:
            runner = MigrationRunner()
            runner.migrate(self._mem_conn)

    def get_connection(self):
        if self.in_memory:
            return _InMemoryConnectionContext(self._mem_conn, self._mem_lock)
        conn = sqlite3.connect(str(self.db_path), timeout=10.0, factory=_FileConnection)
        conn.row_factory = sqlite3.Row
        conn.executescript("PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 5000; PRAGMA synchronous = NORMAL;")
        return conn

    def _init_db(self):
        from kitt.history.migrations import MigrationRunner
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            MigrationRunner().migrate(conn)
        finally:
            conn.close()

    def close(self) -> None:
        """Flush the WAL. Connections are short lived and owned by callers."""
        if self.in_memory:
            with self._mem_lock:
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
