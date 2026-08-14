"""Shared SQLite + FTS5 Repository Index manager."""

from __future__ import annotations

import os
import sqlite3
import hashlib
import time
import re
import threading
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

from kitt.index.schema import INDEX_SCHEMA_SQL, setup_fts5_tables
from kitt.index.scanner import RepositoryScanner
from kitt.index.graph import RepositoryGraph
from kitt.context_engine.parser import SymbolParser

INDEX_SCHEMA_VERSION = "2"
PARSER_REGISTRY_VERSION = "symbol-parser-v1"


class RepositoryIndex:
    """Shared single-instance SQLite repository index for workspace files, symbols, and graph."""

    def __init__(
        self,
        root_dir: str | Path,
        in_memory: bool = False,
        max_files: int = 20000,
        max_file_bytes: int = 512 * 1024,
        max_total_bytes: int = 256 * 1024 * 1024,
    ):
        self.root_path = Path(root_dir).resolve()
        self.in_memory = in_memory or (root_dir == ":memory:")
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes

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
        self._lock = threading.RLock()
        self.last_search_error = ""
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.executescript(INDEX_SCHEMA_SQL)
            self.has_fts5 = setup_fts5_tables(self._conn)
            with self._conn:
                self._set_meta_locked("schema_version", INDEX_SCHEMA_VERSION)
                self._set_meta_locked("parser_registry_version", PARSER_REGISTRY_VERSION)
                self._conn.execute("INSERT OR IGNORE INTO index_meta (key, value) VALUES ('index_generation', '0')")
                self._conn.execute("INSERT OR IGNORE INTO index_meta (key, value) VALUES ('state', 'EMPTY')")
                self._set_meta_locked("workspace_identity", hashlib.sha256(str(self.root_path).encode("utf-8")).hexdigest()[:16])
                self._set_meta_locked("capabilities", json.dumps(self._capabilities(), sort_keys=True))

    def _capabilities(self) -> Dict[str, bool]:
        return {
            "fts5": self.has_fts5,
            "git": (self.root_path / ".git").exists(),
            "kittignore": (self.root_path / ".kittignore").exists(),
            "stdlib_parser": True,
        }

    def _set_meta_locked(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO index_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def metadata(self) -> Dict[str, str]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM index_meta").fetchall()
            return {row["key"]: row["value"] for row in rows}

    def index_generation(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT value FROM index_meta WHERE key='index_generation'").fetchone()
            return int(row["value"]) if row else 0

    def close(self) -> None:
        try:
            with self._lock:
                self._conn.close()
        except Exception:
            pass

    def __del__(self) -> None:
        # ponytail: best-effort cleanup for test/tool instances without owner lifecycle.
        self.close()

    def build_or_update(self) -> Dict[str, int]:
        """Incremental index update based on mtime_ns, size, and content_hash."""
        scanner = RepositoryScanner(self.root_path)
        files = scanner.scan_files(
            max_files=self.max_files,
            max_file_bytes=self.max_file_bytes,
            max_total_bytes=self.max_total_bytes,
        )
        updated_count = 0
        seen_paths = set()

        with self._lock, self._conn:
            self._index_modules_locked(scanner.detect_modules())
            modules = self._module_rows_locked()
            self._conn.execute("UPDATE index_meta SET value='BOOTSTRAP' WHERE key='state'")
            for p in files:
                rel_path = str(p.relative_to(self.root_path))
                seen_paths.add(rel_path)
                stat = p.stat()

                row = self._conn.execute(
                    "SELECT file_id, mtime_ns, size_bytes FROM files WHERE path=?", (rel_path,)
                ).fetchone()

                if row and row["mtime_ns"] == stat.st_mtime_ns and row["size_bytes"] == stat.st_size:
                    continue

                self._index_file_locked(p, rel_path, modules)
                updated_count += 1
            if seen_paths:
                stale = self._conn.execute(
                    "SELECT file_id, path FROM files WHERE path NOT IN (%s)" % ",".join("?" for _ in seen_paths),
                    tuple(seen_paths),
                ).fetchall()
            else:
                stale = self._conn.execute("SELECT file_id, path FROM files").fetchall()
            for row in stale:
                self._delete_file_locked(row["file_id"])
            self._rebuild_reference_edges_locked()
            self._ensure_fts_consistency_locked()
            changed = updated_count or len(stale)
            if changed:
                self._conn.execute(
                    "UPDATE index_meta SET value=CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key='index_generation'"
                )
            state = "READY" if len(files) < self.max_files else "PARTIAL"
            partial_reason = "" if state == "READY" else f"file limit reached ({self.max_files})"
            self._set_meta_locked("state", state)
            self._set_meta_locked("partial_reason", partial_reason)
            self._set_meta_locked("last_scan_at", str(time.time()))

            generation = int(self._conn.execute("SELECT value FROM index_meta WHERE key='index_generation'").fetchone()["value"])
            meta = self.metadata()

        return {
            "scanned": len(files),
            "updated": updated_count,
            "deleted": len(stale),
            "generation": generation,
            "state": meta["state"],
            "freshness": meta.get("last_scan_at", ""),
            "partial_reason": meta.get("partial_reason", ""),
            "schema_version": meta.get("schema_version", ""),
        }

    def update_paths(self, paths: List[str]) -> Dict[str, int]:
        """Synchronously update known changed files without scanning the whole repository."""
        updated = deleted = 0
        with self._lock, self._conn:
            modules = self._module_rows_locked()
            for rel_path in dict.fromkeys(path for path in paths if path and not os.path.isabs(path)):
                path = (self.root_path / rel_path).resolve()
                if self.root_path not in path.parents and path != self.root_path:
                    continue
                row = self._conn.execute("SELECT file_id FROM files WHERE path=?", (rel_path,)).fetchone()
                if not path.is_file():
                    if row:
                        self._delete_file_locked(row["file_id"])
                        deleted += 1
                    continue
                self._index_file_locked(path, rel_path, modules)
                updated += 1
            if updated or deleted:
                self._rebuild_reference_edges_locked()
                self._ensure_fts_consistency_locked()
                self._conn.execute(
                    "UPDATE index_meta SET value=CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key='index_generation'"
                )
                self._set_meta_locked("state", "READY")
                self._set_meta_locked("partial_reason", "")
                self._set_meta_locked("last_scan_at", str(time.time()))
            generation = int(self._conn.execute("SELECT value FROM index_meta WHERE key='index_generation'").fetchone()["value"])
            meta = self.metadata()
        return {
            "scanned": len(paths),
            "updated": updated,
            "deleted": deleted,
            "generation": generation,
            "state": meta["state"],
            "freshness": meta.get("last_scan_at", ""),
            "partial_reason": meta.get("partial_reason", ""),
            "schema_version": meta.get("schema_version", ""),
        }

    def _index_modules(self, modules: List[Dict[str, str]]) -> None:
        with self._lock, self._conn:
            self._index_modules_locked(modules)

    def _index_modules_locked(self, modules: List[Dict[str, str]]) -> None:
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

    def _module_rows(self) -> List[sqlite3.Row]:
        with self._lock:
            return self._module_rows_locked()

    def _module_rows_locked(self) -> List[sqlite3.Row]:
        return self._conn.execute("SELECT module_id, root_path FROM modules ORDER BY length(root_path) DESC").fetchall()

    def _index_file_locked(self, path: Path, rel_path: str, modules: List[sqlite3.Row]) -> None:
        stat = path.stat()
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            content = ""
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        module_id = self._module_id_for_path(rel_path, modules)
        self._conn.execute(
            """
            INSERT INTO files (path, module_id, language, size_bytes, mtime_ns, content_hash, parser_version, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, 'v1', ?)
            ON CONFLICT(path) DO UPDATE SET
                module_id=excluded.module_id,
                mtime_ns=excluded.mtime_ns,
                size_bytes=excluded.size_bytes,
                content_hash=excluded.content_hash,
                indexed_at=excluded.indexed_at
            """,
            (rel_path, module_id, path.suffix.lstrip("."), stat.st_size, stat.st_mtime_ns, content_hash, str(time.time())),
        )
        file_id = self._conn.execute("SELECT file_id FROM files WHERE path=?", (rel_path,)).fetchone()["file_id"]
        self._conn.execute("DELETE FROM refs WHERE file_id=?", (file_id,))
        self._conn.execute("DELETE FROM symbols WHERE file_id=?", (file_id,))
        self._conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
        if self.has_fts5:
            self._conn.execute("DELETE FROM fts_chunks WHERE file_id=?", (file_id,))

        tags = self.parser.extract_file_tags(path, rel_path)
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
                        (file_id, tag.name, tag.name, tag.sub_kind or "symbol", tag.signature, tag.line, tag.line, symbol_hash),
                    )
                    symbol_names.append(tag.name)
                elif tag.kind == "ref":
                    self._conn.execute(
                        "INSERT INTO refs (file_id, target_name, kind, line) VALUES (?, ?, ?, ?)",
                        (file_id, tag.name, tag.sub_kind or "ref", tag.line),
                    )

        lines = content.splitlines() or [""]
        for start in range(0, len(lines), 200):
            chunk_content = "\n".join(lines[start:start + 200])
            chunk_hash = hashlib.sha256(chunk_content.encode("utf-8")).hexdigest()
            end_line = start + len(lines[start:start + 200])
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

    def _delete_file_locked(self, file_id: int) -> None:
        if self.has_fts5:
            self._conn.execute("DELETE FROM fts_chunks WHERE file_id=?", (file_id,))
        self._conn.execute("DELETE FROM files WHERE file_id=?", (file_id,))

    def _ensure_fts_consistency_locked(self) -> None:
        if not self.has_fts5:
            return
        chunk_count = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        fts_count = self._conn.execute("SELECT COUNT(*) FROM fts_chunks").fetchone()[0]
        if chunk_count == fts_count:
            return
        self._conn.execute("DELETE FROM fts_chunks")
        rows = self._conn.execute(
            """
            SELECT c.chunk_id, c.file_id, f.path, c.content,
                   COALESCE(group_concat(s.name, ' '), '') AS symbols
            FROM chunks c
            JOIN files f ON f.file_id = c.file_id
            LEFT JOIN symbols s ON s.file_id = c.file_id
            GROUP BY c.chunk_id, c.file_id, f.path, c.content
            """
        ).fetchall()
        self._conn.executemany(
            "INSERT INTO fts_chunks(rowid, chunk_id, file_id, path, symbol_name, content) VALUES (?, ?, ?, ?, ?, ?)",
            [(r["chunk_id"], r["chunk_id"], r["file_id"], r["path"], r["symbols"], r["content"]) for r in rows],
        )

    @staticmethod
    def _module_id_for_path(rel_path: str, modules: List[sqlite3.Row]) -> Optional[int]:
        for module in modules:
            root = module["root_path"]
            if root == "." or rel_path == root or rel_path.startswith(root.rstrip("/") + "/"):
                return module["module_id"]
        return None

    def _rebuild_reference_edges(self) -> None:
        with self._lock, self._conn:
            self._rebuild_reference_edges_locked()

    def _rebuild_reference_edges_locked(self) -> None:
        self._conn.execute("DELETE FROM edges")
        self.graph = RepositoryGraph()
        rows = self._conn.execute(
            """
            SELECT rf.file_id AS source_file_id, sf.file_id AS target_file_id, r.kind
            FROM refs r
            JOIN files rf ON rf.file_id = r.file_id
            JOIN symbols s ON s.name = r.target_name OR s.qualified_name = r.target_name
            JOIN files sf ON sf.file_id = s.file_id
            WHERE rf.file_id != sf.file_id
            """
        ).fetchall()
        for row in rows:
            self._conn.execute(
                """
                INSERT INTO edges (source_file_id, target_file_id, kind, weight)
                VALUES (?, ?, ?, 1.0)
                ON CONFLICT(source_file_id, target_file_id, kind) DO UPDATE SET
                    weight=excluded.weight
                """,
                (row["source_file_id"], row["target_file_id"], row["kind"]),
            )
        path_edges = self._conn.execute(
            """
            SELECT sf.path AS source_path, tf.path AS target_path, e.weight
            FROM edges e
            JOIN files sf ON sf.file_id = e.source_file_id
            JOIN files tf ON tf.file_id = e.target_file_id
            """
        ).fetchall()
        for row in path_edges:
            self.graph.add_edge(row["source_path"], row["target_path"], row["weight"])

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
        with self._lock:
            results = []
            self.last_search_error = ""
            if self.has_fts5:
                try:
                    fts_query = self._fts_query(query)
                    if not fts_query:
                        return []
                    rows = self._conn.execute(
                        """
                        SELECT fts_chunks.path, fts_chunks.content, c.start_line, c.end_line, c.content_hash,
                               bm25(fts_chunks, 2.0, 2.5, 1.0) AS score
                        FROM fts_chunks
                        JOIN chunks c ON c.chunk_id = fts_chunks.chunk_id
                        WHERE fts_chunks MATCH ?
                        ORDER BY score
                        LIMIT ?
                        """,
                        (fts_query, limit)
                    ).fetchall()
                    for r in rows:
                        results.append({
                            "path": r["path"], "content": r["content"], "method": "fts5", "score": r["score"],
                            "start_line": r["start_line"], "end_line": r["end_line"], "content_hash": r["content_hash"],
                        })
                    return results
                except sqlite3.Error as exc:
                    self.last_search_error = f"fts5_error: {exc}"

            # Lexical fallback
            terms = self._query_terms(query)
            if not terms:
                return []
            where = " AND ".join("c.content LIKE ?" for _ in terms)
            rows = self._conn.execute(
                f"""
                SELECT f.path, c.content, c.start_line, c.end_line, c.content_hash
                FROM chunks c JOIN files f ON c.file_id = f.file_id
                WHERE {where}
                LIMIT ?
                """,
                tuple(f"%{term}%" for term in terms) + (limit,)
            ).fetchall()
            for r in rows:
                results.append({
                    "path": r["path"], "content": r["content"], "method": "lexical",
                    "start_line": r["start_line"], "end_line": r["end_line"], "content_hash": r["content_hash"],
                })
            return [r for r in results if r.get("path")]

    def search_symbol(self, symbol: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Return chunks containing exact symbol definitions."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT f.path, s.name, s.qualified_name, s.kind, s.signature,
                       c.content, c.start_line, c.end_line, c.content_hash
                FROM symbols s
                JOIN files f ON f.file_id = s.file_id
                JOIN chunks c ON c.file_id = f.file_id
                     AND c.start_line <= s.start_line AND c.end_line >= s.start_line
                WHERE s.name = ? OR s.qualified_name = ?
                ORDER BY s.start_line
                LIMIT ?
                """,
                (symbol, symbol, limit),
            ).fetchall()
            return [
                {
                    "path": row["path"],
                    "symbol": row["name"],
                    "qualified_name": row["qualified_name"],
                    "kind": row["kind"],
                    "signature": row["signature"],
                    "content": row["content"],
                    "method": "symbol",
                    "start_line": row["start_line"],
                    "end_line": row["end_line"],
                    "content_hash": row["content_hash"],
                }
                for row in rows
            ]
