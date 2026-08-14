"""SQLite + FTS5 Repository Index Schema Definition."""

from __future__ import annotations

import sqlite3

INDEX_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS index_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS modules (
    module_id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_path TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    manifest_path TEXT,
    content_hash TEXT
);

CREATE TABLE IF NOT EXISTS files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id INTEGER REFERENCES modules(module_id) ON DELETE SET NULL,
    path TEXT NOT NULL UNIQUE,
    language TEXT,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    generated INTEGER NOT NULL DEFAULT 0,
    binary INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS symbols (
    symbol_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    qualified_name TEXT,
    kind TEXT NOT NULL,
    signature TEXT,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    parent_symbol_id INTEGER REFERENCES symbols(symbol_id),
    symbol_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refs (
    ref_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    source_symbol_id INTEGER REFERENCES symbols(symbol_id) ON DELETE CASCADE,
    target_name TEXT NOT NULL,
    target_symbol_id INTEGER REFERENCES symbols(symbol_id),
    kind TEXT NOT NULL,
    line INTEGER
);

CREATE TABLE IF NOT EXISTS edges (
    source_file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    target_file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    weight REAL NOT NULL,
    PRIMARY KEY(source_file_id, target_file_id, kind)
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    symbol_id INTEGER REFERENCES symbols(symbol_id) ON DELETE CASCADE,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_refs_target_name ON refs(target_name);
CREATE INDEX IF NOT EXISTS idx_refs_file ON refs(file_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_file_id);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_file_id);
"""


def check_fts5_supported(conn: sqlite3.Connection) -> bool:
    """Check if SQLite connection has FTS5 support enabled."""
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp._fts_check USING fts5(content);")
        conn.execute("DROP TABLE temp._fts_check;")
        return True
    except sqlite3.OperationalError:
        return False


def setup_fts5_tables(conn: sqlite3.Connection) -> bool:
    """Setup FTS5 virtual table if supported, returns True on success."""
    if not check_fts5_supported(conn):
        return False
    try:
        required = {"chunk_id", "file_id", "path", "symbol_name", "content"}
        existing = {row[1] for row in conn.execute("PRAGMA table_info(fts_chunks)").fetchall()}
        if existing and not required.issubset(existing):
            conn.execute("DROP TABLE IF EXISTS fts_chunks")
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(
                chunk_id UNINDEXED,
                file_id UNINDEXED,
                path,
                symbol_name,
                content,
                tokenize = "unicode61 tokenchars '_.$#'",
                prefix = '2 3 4'
            );
        """)
        return True
    except sqlite3.OperationalError:
        return False
