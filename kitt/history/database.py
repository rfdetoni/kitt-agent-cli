import os
import sqlite3
import hashlib
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1

CREATE_TABLES_SQL = """
PRAGMA foreign_keys = ON;

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
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
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
"""

class HistoryDatabase:
    """SQLite database manager for persistent workspace conversation history."""

    def __init__(self, root_dir: str = "."):
        self.root_path = Path(root_dir).resolve()
        self.kitt_dir = self.root_path / ".kitt" / "history"
        self.kitt_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.kitt_dir / "history.sqlite3"
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self):
        with self.get_connection() as conn:
            conn.executescript(CREATE_TABLES_SQL)
            cur = conn.cursor()
            cur.execute("SELECT version FROM schema_info LIMIT 1;")
            row = cur.fetchone()
            if not row:
                cur.execute("INSERT INTO schema_info (version) VALUES (?);", (SCHEMA_VERSION,))
