import hashlib
import json
import time
import uuid
from typing import Any, Dict, List, Optional

from kitt.history.database import HistoryDatabase
from kitt.history.models import SessionEntry, SessionEntryType


class SessionTreeRepository:
    """Persistent branching transcript with an explicit active cursor."""

    def __init__(self, db: HistoryDatabase):
        self.db = db

    @staticmethod
    def _hash_payload(payload: Dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _row(row) -> SessionEntry:
        return SessionEntry(
            id=row["id"], conversation_id=row["conversation_id"],
            parent_entry_id=row["parent_entry_id"], turn_id=row["turn_id"],
            entry_type=row["entry_type"], payload=json.loads(row["payload_json"]),
            include_in_context=bool(row["include_in_context"]),
            generation=row["generation"], created_at=row["created_at"],
            content_hash=row["content_hash"],
        )

    def append_entry(self, conversation_id: str, entry_type: SessionEntryType,
                     payload: Dict[str, Any], turn_id: Optional[str] = None,
                     parent_entry_id: Optional[str] = None,
                     include_in_context: bool = True,
                     use_active_parent: bool = True) -> SessionEntry:
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conv = conn.execute(
                "SELECT active_entry_id FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if not conv:
                raise ValueError("Conversation not found")
            if parent_entry_id is None and use_active_parent:
                parent_entry_id = conv["active_entry_id"]
            generation = 0
            if parent_entry_id:
                parent = conn.execute(
                    "SELECT conversation_id, generation FROM session_entries WHERE id = ?",
                    (parent_entry_id,),
                ).fetchone()
                if not parent or parent["conversation_id"] != conversation_id:
                    raise ValueError("Parent entry does not belong to conversation")
                generation = parent["generation"] + 1
            entry = SessionEntry(
                id=f"entry_{uuid.uuid4().hex}", conversation_id=conversation_id,
                parent_entry_id=parent_entry_id, turn_id=turn_id,
                entry_type=entry_type, payload=dict(payload),
                include_in_context=include_in_context, generation=generation,
                created_at=now, content_hash=self._hash_payload(payload),
            )
            conn.execute(
                """INSERT INTO session_entries
                (id, conversation_id, parent_entry_id, turn_id, entry_type, payload_json,
                 include_in_context, generation, created_at, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (entry.id, conversation_id, parent_entry_id, turn_id, entry_type,
                 json.dumps(payload, ensure_ascii=False), int(include_in_context),
                 generation, now, entry.content_hash),
            )
            conn.execute(
                """UPDATE conversations SET active_entry_id = ?, active_generation = ?,
                   updated_at = ? WHERE id = ?""",
                (entry.id, generation, now, conversation_id),
            )
        return entry

    def get_entry(self, entry_id: str) -> Optional[SessionEntry]:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM session_entries WHERE id = ?", (entry_id,)).fetchone()
            return self._row(row) if row else None

    def get_active_entry(self, conversation_id: str) -> Optional[SessionEntry]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                """SELECT e.* FROM conversations c LEFT JOIN session_entries e
                   ON e.id = c.active_entry_id WHERE c.id = ?""", (conversation_id,)
            ).fetchone()
            return self._row(row) if row and row["id"] else None

    def set_active_entry(self, conversation_id: str, entry_id: str) -> None:
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT conversation_id, generation FROM session_entries WHERE id = ?", (entry_id,)
            ).fetchone()
            if not row or row["conversation_id"] != conversation_id:
                raise ValueError("Entry does not belong to conversation")
            conn.execute(
                "UPDATE conversations SET active_entry_id=?, active_generation=?, updated_at=? WHERE id=?",
                (entry_id, row["generation"], time.time(), conversation_id),
            )

    def get_active_path(self, conversation_id: str, max_depth: int = 10000) -> List[SessionEntry]:
        current = self.get_active_entry(conversation_id)
        path: List[SessionEntry] = []
        seen = set()
        while current:
            if current.id in seen or len(path) >= max_depth:
                raise ValueError("Cycle or excessive depth in session tree")
            seen.add(current.id)
            path.append(current)
            if not current.parent_entry_id:
                break
            current = self.get_entry(current.parent_entry_id)
            if current and current.conversation_id != conversation_id:
                raise ValueError("Cross-conversation session parent")
        return list(reversed(path))

    def list_children(self, entry_id: str) -> List[SessionEntry]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM session_entries WHERE parent_entry_id=? ORDER BY created_at,id", (entry_id,)
            ).fetchall()
            return [self._row(row) for row in rows]

    def label_entry(self, entry_id: str, label: str) -> SessionEntry:
        entry = self.get_entry(entry_id)
        if not entry:
            raise ValueError("Entry not found")
        return self.append_entry(entry.conversation_id, "LABEL", {"label": label}, parent_entry_id=entry_id)

    def find_entries(self, conversation_id: str, query: str, limit: int = 20,
                     offset: int = 0) -> List[SessionEntry]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """SELECT * FROM session_entries WHERE conversation_id=? AND payload_json LIKE ?
                   ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?""",
                (conversation_id, f"%{query}%", min(max(limit, 1), 100), max(offset, 0)),
            ).fetchall()
            return [self._row(row) for row in rows]

    def clone_active_path(self, source_conversation_id: str, target_conversation_id: str) -> None:
        """Clone the active path into the target conversation atomically.

        The whole clone runs inside a single ``BEGIN IMMEDIATE`` transaction so a
        failure cannot leave a partial copy behind.  The target active cursor is
        moved to the cloned leaf.
        """
        source_entries = self.get_active_path(source_conversation_id)
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            parent = None
            cloned_ids = []
            generation = 0
            for source in source_entries:
                generation += 1
                entry = SessionEntry(
                    id=f"entry_{uuid.uuid4().hex}", conversation_id=target_conversation_id,
                    parent_entry_id=parent, turn_id=source.turn_id,
                    entry_type=source.entry_type, payload=dict(source.payload),
                    include_in_context=source.include_in_context, generation=generation,
                    created_at=now, content_hash=source.content_hash,
                )
                conn.execute(
                    """INSERT INTO session_entries
                    (id, conversation_id, parent_entry_id, turn_id, entry_type, payload_json,
                     include_in_context, generation, created_at, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (entry.id, target_conversation_id, parent, source.turn_id, source.entry_type,
                     json.dumps(source.payload, ensure_ascii=False), int(source.include_in_context),
                     generation, now, source.content_hash),
                )
                parent = entry.id
                cloned_ids.append(entry.id)
            if cloned_ids:
                conn.execute(
                    """UPDATE conversations SET active_entry_id = ?, active_generation = ?,
                       updated_at = ? WHERE id = ?""",
                    (cloned_ids[-1], generation, now, target_conversation_id),
                )
