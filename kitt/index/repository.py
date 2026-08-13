"""Shared SQLite + FTS5 Repository Index manager."""

from __future__ import annotations

import os
import sqlite3
import hashlib
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

from kitt.index.schema import INDEX_SCHEMA_SQL, setup_fts5_tables
from kitt.index.scanner import RepositoryScanner
from kitt.index.graph import RepositoryGraph


class RepositoryIndex:
    """Shared single-instance SQLite repository index for workspace files, symbols, and graph."""

    def __init__(self, root_dir: str | Path, in_memory: bool = False):
        self.root_path = Path(root_dir).resolve()
        self.in_memory = in_memory or (root_dir == ":memory:")

        if self.in_memory:
            self.db_path = ":memory:"
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        else:
            index_dir = self.root_path / ".kitt" / "index"
            index_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = index_dir / "index.db"
            self._conn = sqlite3.connect(str(self.db_path), timeout=10.0, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row

        self.has_fts5 = False
        self.graph = RepositoryGraph()
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript(INDEX_SCHEMA_SQL)
        self.has_fts5 = setup_fts5_tables(self._conn)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def build_or_update(self) -> Dict[str, int]:
        """Incremental index update based on mtime_ns, size, and content_hash."""
        scanner = RepositoryScanner(self.root_path)
        files = scanner.scan_files()
        updated_count = 0

        with self._conn:
            for p in files:
                rel_path = str(p.relative_to(self.root_path))
                stat = p.stat()
                mtime_ns = stat.st_mtime_ns
                size = stat.st_size

                row = self._conn.execute(
                    "SELECT file_id, mtime_ns, size_bytes FROM files WHERE path=?", (rel_path,)
                ).fetchone()

                if row and row["mtime_ns"] == mtime_ns and row["size_bytes"] == size:
                    continue

                try:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    content = ""

                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                now = str(time.time())

                self._conn.execute(
                    """
                    INSERT INTO files (path, language, size_bytes, mtime_ns, content_hash, parser_version, indexed_at)
                    VALUES (?, ?, ?, ?, ?, 'v1', ?)
                    ON CONFLICT(path) DO UPDATE SET
                        mtime_ns=excluded.mtime_ns,
                        size_bytes=excluded.size_bytes,
                        content_hash=excluded.content_hash,
                        indexed_at=excluded.indexed_at
                    """,
                    (rel_path, p.suffix.lstrip('.'), size, mtime_ns, content_hash, now)
                )
                file_row = self._conn.execute("SELECT file_id FROM files WHERE path=?", (rel_path,)).fetchone()
                file_id = file_row["file_id"]

                # Extract basic symbols/chunks for FTS
                self._conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
                self._conn.execute(
                    "INSERT INTO chunks (file_id, start_line, end_line, content, content_hash) VALUES (?, 1, ?, ?, ?)",
                    (file_id, len(content.splitlines()), content[:4000], content_hash)
                )

                if self.has_fts5:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO fts_chunks(rowid, path, symbol_name, content) VALUES (?, ?, ?, ?)",
                        (file_id, rel_path, "", content[:4000])
                    )
                updated_count += 1

        return {"scanned": len(files), "updated": updated_count}

    def search_text(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search code chunks using FTS5 or fallback lexical query."""
        results = []
        if self.has_fts5:
            try:
                rows = self._conn.execute(
                    "SELECT path, content FROM fts_chunks WHERE fts_chunks MATCH ? LIMIT ?",
                    (query, limit)
                ).fetchall()
                for r in rows:
                    results.append({"path": r["path"], "content": r["content"], "method": "fts5"})
                return results
            except Exception:
                pass

        # Lexical fallback
        like_pattern = f"%{query}%"
        rows = self._conn.execute(
            "SELECT f.path, c.content FROM chunks c JOIN files f ON c.file_id = f.file_id WHERE c.content LIKE ? LIMIT ?",
            (like_pattern, limit)
        ).fetchall()
        for r in rows:
            results.append({"path": r["path"], "content": r["content"], "method": "lexical"})
        return results
