from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List

from kitt.history.database import HistoryDatabase


_TOKEN_RE = re.compile(r"[^\W_]+(?:[._:/-][^\W_]+)*", re.UNICODE)


@dataclass(frozen=True)
class HistorySearchStatus:
    backend: str
    indexed_rowid: int


class HistorySearchIndex:
    """Incremental, disposable full-text index for persisted conversation history.

    The canonical source remains ``messages``/``conversations``. The FTS table
    is only a derived cache: it can be rebuilt without touching canonical data.
    Searches are workspace-scoped and return bounded snippets, never whole
    conversations or complete message histories.
    """

    MAX_QUERY_CHARS = 512
    MAX_RESULTS = 50
    MAX_SNIPPET_CHARS = 480
    FALLBACK_CONVERSATIONS = 500

    def __init__(self, db: HistoryDatabase):
        self.db = db
        self._fts_available: bool | None = None

    @staticmethod
    def _tokens(query: str) -> list[str]:
        return [token for token in _TOKEN_RE.findall(query.casefold()) if token][:24]

    @classmethod
    def _fts_query(cls, query: str) -> str:
        tokens = cls._tokens(query)
        return " AND ".join(
            f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens
        )

    @staticmethod
    def _like_term(query: str) -> str:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return "%" + escaped + "%"

    def _ensure_fts(self, conn: sqlite3.Connection) -> bool:
        if self._fts_available is False:
            return False
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS history_message_fts USING fts5(
                    conversation_id UNINDEXED,
                    workspace_id UNINDEXED,
                    role UNINDEXED,
                    content,
                    tokenize='unicode61 remove_diacritics 2'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS history_search_meta (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    indexed_rowid INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO history_search_meta(singleton, indexed_rowid) VALUES (1, 0)"
            )
            # These triggers keep the disposable index current after its first
            # creation. Existing rows are backfilled by _sync().
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS history_message_fts_ai
                AFTER INSERT ON messages BEGIN
                    INSERT OR REPLACE INTO history_message_fts(
                        rowid, conversation_id, workspace_id, role, content
                    ) VALUES (
                        new.rowid,
                        new.conversation_id,
                        (SELECT workspace_id FROM conversations WHERE id = new.conversation_id),
                        new.role,
                        new.content
                    );
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS history_message_fts_ad
                AFTER DELETE ON messages BEGIN
                    DELETE FROM history_message_fts WHERE rowid = old.rowid;
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS history_message_fts_au
                AFTER UPDATE OF conversation_id, role, content ON messages BEGIN
                    DELETE FROM history_message_fts WHERE rowid = old.rowid;
                    INSERT OR REPLACE INTO history_message_fts(
                        rowid, conversation_id, workspace_id, role, content
                    ) VALUES (
                        new.rowid,
                        new.conversation_id,
                        (SELECT workspace_id FROM conversations WHERE id = new.conversation_id),
                        new.role,
                        new.content
                    );
                END
                """
            )
            self._fts_available = True
            return True
        except sqlite3.OperationalError as exc:
            if "fts5" not in str(exc).casefold():
                raise
            self._fts_available = False
            return False

    def _sync(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT indexed_rowid FROM history_search_meta WHERE singleton = 1"
        ).fetchone()
        indexed_rowid = int(row[0] if row else 0)
        max_row = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM messages").fetchone()
        max_rowid = int(max_row[0] if max_row else 0)

        # Recover automatically if the derived FTS table was manually removed
        # and recreated while its tiny metadata table survived.
        if indexed_rowid > 0:
            indexed_count = int(
                conn.execute("SELECT COUNT(*) FROM history_message_fts").fetchone()[0]
            )
            message_count = int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
            if message_count > 0 and indexed_count == 0:
                indexed_rowid = 0
                conn.execute(
                    "UPDATE history_search_meta SET indexed_rowid = 0 WHERE singleton = 1"
                )

        if max_rowid > indexed_rowid:
            conn.execute(
                """
                INSERT OR REPLACE INTO history_message_fts(
                    rowid, conversation_id, workspace_id, role, content
                )
                SELECT m.rowid, m.conversation_id, c.workspace_id, m.role, m.content
                  FROM messages AS m
                  JOIN conversations AS c ON c.id = m.conversation_id
                 WHERE m.rowid > ? AND m.rowid <= ?
                """,
                (indexed_rowid, max_rowid),
            )
            indexed_rowid = max_rowid
            conn.execute(
                "UPDATE history_search_meta SET indexed_rowid = ? WHERE singleton = 1",
                (indexed_rowid,),
            )
        return indexed_rowid

    @classmethod
    def _bounded_snippet(cls, value: Any) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= cls.MAX_SNIPPET_CHARS:
            return text
        return text[: cls.MAX_SNIPPET_CHARS - 1].rstrip() + "…"

    def search(
        self,
        workspace_id: str,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        query = str(query or "").strip()[: self.MAX_QUERY_CHARS]
        if not query:
            return []
        limit = max(1, min(int(limit), self.MAX_RESULTS))
        offset = max(0, int(offset))

        with self.db.get_connection() as conn:
            if self._ensure_fts(conn):
                indexed_rowid = self._sync(conn)
                rows = self._search_fts(conn, workspace_id, query, limit, offset)
                for row in rows:
                    row["search_backend"] = "fts5"
                    row["indexed_rowid"] = indexed_rowid
                return rows
            rows = self._search_fallback(conn, workspace_id, query, limit, offset)
            for row in rows:
                row["search_backend"] = "bounded_like"
                row["indexed_rowid"] = 0
            return rows

    def status(self) -> HistorySearchStatus:
        with self.db.get_connection() as conn:
            if not self._ensure_fts(conn):
                return HistorySearchStatus("bounded_like", 0)
            return HistorySearchStatus("fts5", self._sync(conn))

    def _search_fts(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        query: str,
        limit: int,
        offset: int,
    ) -> List[Dict[str, Any]]:
        fts_query = self._fts_query(query)
        if not fts_query:
            return self._search_fallback(conn, workspace_id, query, limit, offset)

        # Overfetch a bounded number of message hits, then collapse them to the
        # best hit per conversation. This prevents a long conversation from
        # monopolizing the result set.
        raw_limit = min(250, max(limit * 6, 30) + offset)
        message_rows = conn.execute(
            """
            SELECT c.id, c.workspace_id, c.title, c.status, c.parent_conversation_id,
                   c.created_at, c.updated_at, c.compact_summary,
                   f.role AS match_role,
                   snippet(history_message_fts, 3, '', '', ' … ', 28) AS match_snippet,
                   bm25(history_message_fts) AS rank
              FROM history_message_fts AS f
              JOIN conversations AS c ON c.id = f.conversation_id
             WHERE history_message_fts MATCH ?
               AND f.workspace_id = ?
               AND c.workspace_id = ?
               AND c.status NOT IN ('DELETED', 'INTERNAL_CHILD')
             ORDER BY rank ASC, c.updated_at DESC, c.id DESC
             LIMIT ?
            """,
            (fts_query, workspace_id, workspace_id, raw_limit),
        ).fetchall()

        like = self._like_term(query)
        title_rows = conn.execute(
            """
            SELECT c.id, c.workspace_id, c.title, c.status, c.parent_conversation_id,
                   c.created_at, c.updated_at, c.compact_summary,
                   NULL AS match_role,
                   COALESCE(c.compact_summary, c.title) AS match_snippet,
                   -100.0 AS rank
              FROM conversations AS c
             WHERE c.workspace_id = ?
               AND c.status NOT IN ('DELETED', 'INTERNAL_CHILD')
               AND (c.title LIKE ? ESCAPE '\\' OR c.compact_summary LIKE ? ESCAPE '\\')
             ORDER BY c.updated_at DESC, c.id DESC
             LIMIT ?
            """,
            (workspace_id, like, like, min(100, raw_limit)),
        ).fetchall()

        best: dict[str, Dict[str, Any]] = {}
        for source, rows in (("metadata", title_rows), ("message", message_rows)):
            for raw in rows:
                row = dict(raw)
                conv_id = str(row["id"])
                if conv_id in best:
                    continue
                row["match_source"] = source
                row["match_snippet"] = self._bounded_snippet(row.get("match_snippet"))
                row["search_score"] = float(row.pop("rank", 0.0) or 0.0)
                best[conv_id] = row

        ranked = sorted(
            best.values(),
            key=lambda item: (
                0 if item["match_source"] == "metadata" else 1,
                float(item.get("search_score", 0.0)),
                -float(item.get("updated_at", 0.0) or 0.0),
                str(item.get("id", "")),
            ),
        )
        return ranked[offset : offset + limit]

    def _search_fallback(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        query: str,
        limit: int,
        offset: int,
    ) -> List[Dict[str, Any]]:
        like = self._like_term(query)
        rows = conn.execute(
            """
            WITH candidates AS (
                SELECT id, workspace_id, title, status, parent_conversation_id,
                       created_at, updated_at, compact_summary
                  FROM conversations
                 WHERE workspace_id = ?
                   AND status NOT IN ('DELETED', 'INTERNAL_CHILD')
                 ORDER BY updated_at DESC, id DESC
                 LIMIT ?
            )
            SELECT c.*,
                   CASE
                     WHEN c.title LIKE ? ESCAPE '\\' OR c.compact_summary LIKE ? ESCAPE '\\'
                     THEN 'metadata' ELSE 'message'
                   END AS match_source,
                   (
                     SELECT m.role FROM messages AS m
                      WHERE m.conversation_id = c.id
                        AND m.content LIKE ? ESCAPE '\\'
                      ORDER BY m.created_at DESC, m.id DESC LIMIT 1
                   ) AS match_role,
                   COALESCE(
                     CASE WHEN c.title LIKE ? ESCAPE '\\' THEN c.title END,
                     CASE WHEN c.compact_summary LIKE ? ESCAPE '\\' THEN c.compact_summary END,
                     (
                       SELECT m.content FROM messages AS m
                        WHERE m.conversation_id = c.id
                          AND m.content LIKE ? ESCAPE '\\'
                        ORDER BY m.created_at DESC, m.id DESC LIMIT 1
                     )
                   ) AS match_snippet
              FROM candidates AS c
             WHERE c.title LIKE ? ESCAPE '\\'
                OR c.compact_summary LIKE ? ESCAPE '\\'
                OR EXISTS (
                     SELECT 1 FROM messages AS m
                      WHERE m.conversation_id = c.id
                        AND m.content LIKE ? ESCAPE '\\'
                   )
             ORDER BY c.updated_at DESC, c.id DESC
             LIMIT ? OFFSET ?
            """,
            (
                workspace_id,
                self.FALLBACK_CONVERSATIONS,
                like, like, like,
                like, like, like,
                like, like, like,
                limit, offset,
            ),
        ).fetchall()
        result = []
        for raw in rows:
            row = dict(raw)
            row["match_snippet"] = self._bounded_snippet(row.get("match_snippet"))
            row["search_score"] = 0.0
            result.append(row)
        return result
