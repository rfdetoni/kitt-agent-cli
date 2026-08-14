"""Shared SQLite + FTS5 Repository Index manager."""

from __future__ import annotations

import os
import sqlite3
import hashlib
import time
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from kitt.index.schema import INDEX_SCHEMA_SQL, setup_fts5_tables
from kitt.index.scanner import RepositoryScanner
from kitt.index.graph import RepositoryGraph
from kitt.context_engine.parser import SymbolParser


class RepositoryIndex:
    """Shared single-instance SQLite repository index for workspace files, symbols, and graph."""

    def __init__(self, root_dir: str | Path, in_memory: bool = False, max_files: int = 20000):
        self.root_path = Path(root_dir).resolve()
        self.in_memory = in_memory or (root_dir == ":memory:")
        self.max_files = max_files

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
        self.parser = SymbolParser()
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
        self._index_modules(scanner.detect_modules())
        files = scanner.scan_files(max_files=self.max_files)
        updated_count = 0
        seen_paths = set()

        with self._conn:
            for p in files:
                rel_path = str(p.relative_to(self.root_path))
                seen_paths.add(rel_path)
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
                self._conn.execute("DELETE FROM refs WHERE file_id=?", (file_id,))
                self._conn.execute("DELETE FROM symbols WHERE file_id=?", (file_id,))
                self._conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
                if self.has_fts5:
                    self._conn.execute("DELETE FROM fts_chunks WHERE file_id=?", (file_id,))

                tags = self.parser.extract_file_tags(p, rel_path)
                symbol_names = []
                if tags:
                    for tag in tags.tags:
                        if tag.kind == "def":
                            symbol_hash = hashlib.sha256(f"{content_hash}:{tag.name}:{tag.line}".encode("utf-8")).hexdigest()
                            self._conn.execute(
                                """
                                INSERT INTO symbols
                                    (file_id, name, qualified_name, kind, signature, start_line, end_line, symbol_hash)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    file_id, tag.name, tag.name, tag.sub_kind or "symbol",
                                    tag.signature, tag.line, tag.line, symbol_hash,
                                ),
                            )
                            symbol_names.append(tag.name)
                        elif tag.kind == "ref":
                            self._conn.execute(
                                "INSERT INTO refs (file_id, target_name, kind, line) VALUES (?, ?, ?, ?)",
                                (file_id, tag.name, tag.sub_kind or "ref", tag.line),
                            )

                lines = content.splitlines()
                if not lines:
                    lines = [""]
                for start in range(0, len(lines), 200):
                    chunk_lines = lines[start:start + 200]
                    chunk_content = "\n".join(chunk_lines)
                    chunk_hash = hashlib.sha256(chunk_content.encode("utf-8")).hexdigest()
                    end_line = start + len(chunk_lines)
                    cur = self._conn.execute(
                        "INSERT INTO chunks (file_id, start_line, end_line, content, content_hash) VALUES (?, ?, ?, ?, ?)",
                        (file_id, start + 1, end_line, chunk_content, chunk_hash),
                    )
                    chunk_id = cur.lastrowid
                    if self.has_fts5:
                        self._conn.execute(
                            "INSERT INTO fts_chunks(rowid, chunk_id, file_id, path, symbol_name, content) VALUES (?, ?, ?, ?, ?, ?)",
                            (chunk_id, chunk_id, file_id, rel_path, " ".join(symbol_names), chunk_content),
                        )
                updated_count += 1
            if seen_paths:
                stale = self._conn.execute(
                    "SELECT file_id, path FROM files WHERE path NOT IN (%s)" % ",".join("?" for _ in seen_paths),
                    tuple(seen_paths),
                ).fetchall()
            else:
                stale = self._conn.execute("SELECT file_id, path FROM files").fetchall()
            for row in stale:
                if self.has_fts5:
                    self._conn.execute("DELETE FROM fts_chunks WHERE file_id=?", (row["file_id"],))
                self._conn.execute("DELETE FROM files WHERE file_id=?", (row["file_id"],))

        return {"scanned": len(files), "updated": updated_count, "deleted": len(stale)}

    def _index_modules(self, modules: List[Dict[str, str]]) -> None:
        with self._conn:
            for module in modules:
                manifest = module.get("manifest_path")
                digest = ""
                if manifest:
                    path = self.root_path / manifest
                    if path.exists():
                        try:
                            digest = hashlib.sha256(path.read_bytes()).hexdigest()
                        except OSError:
                            digest = ""
                self._conn.execute(
                    """
                    INSERT INTO modules (root_path, kind, manifest_path, content_hash)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(root_path) DO UPDATE SET
                        kind=excluded.kind,
                        manifest_path=excluded.manifest_path,
                        content_hash=excluded.content_hash
                    """,
                    (module["root_path"], module["kind"], manifest, digest),
                )

    @staticmethod
    def _query_terms(query: str) -> List[str]:
        terms = re.findall(r"[A-Za-z0-9_.$#]{2,}", query)
        base_terms = list(terms)
        if len(base_terms) > 1:
            terms.append("_".join(base_terms))
            terms.extend("_".join(pair) for pair in zip(base_terms, base_terms[1:]))
        seen = set()
        terms = [term for term in terms if not (term in seen or seen.add(term))]
        return terms[:8]

    @classmethod
    def _fts_query(cls, query: str) -> str:
        terms = cls._query_terms(query)
        return " OR ".join(f'"{term.replace(chr(34), chr(34) + chr(34))}"' for term in terms)

    def search_text(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search code chunks using FTS5 or fallback lexical query."""
        results = []
        if self.has_fts5:
            try:
                fts_query = self._fts_query(query)
                if not fts_query:
                    return []
                rows = self._conn.execute(
                    """
                    SELECT path, content, bm25(fts_chunks, 2.0, 2.5, 1.0) AS score
                    FROM fts_chunks
                    WHERE fts_chunks MATCH ?
                    ORDER BY score
                    LIMIT ?
                    """,
                    (fts_query, limit)
                ).fetchall()
                for r in rows:
                    results.append({"path": r["path"], "content": r["content"], "method": "fts5", "score": r["score"]})
                return results
            except sqlite3.Error as exc:
                results.append({"path": "", "content": "", "method": "fts5_error", "error": str(exc)})

        # Lexical fallback
        terms = self._query_terms(query)
        if not terms:
            return [r for r in results if r.get("path")]
        where = " AND ".join("c.content LIKE ?" for _ in terms)
        rows = self._conn.execute(
            f"SELECT f.path, c.content FROM chunks c JOIN files f ON c.file_id = f.file_id WHERE {where} LIMIT ?",
            tuple(f"%{term}%" for term in terms) + (limit,)
        ).fetchall()
        for r in rows:
            results.append({"path": r["path"], "content": r["content"], "method": "lexical"})
        return [r for r in results if r.get("path")]
