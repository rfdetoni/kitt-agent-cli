import uuid
import time
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional
from kitt.history.database import HistoryDatabase

class HistoryRepository:
    """Data access repository for SQLite history entities."""

    def __init__(self, db: HistoryDatabase):
        self.db = db

    def get_or_create_workspace(self, root_path: str) -> Dict[str, Any]:
        path_hash = hashlib.sha256(root_path.encode('utf-8')).hexdigest()
        display_name = str(Path(root_path).name)
        now = time.time()

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM workspaces WHERE canonical_path_hash = ?;", (path_hash,))
            row = cur.fetchone()
            if row:
                ws_dict = dict(row)
                cur.execute("UPDATE workspaces SET last_opened_at = ? WHERE id = ?;", (now, ws_dict["id"]))
                return ws_dict

            ws_id = uuid.uuid4().hex
            cur.execute(
                "INSERT INTO workspaces (id, canonical_path_hash, display_name, git_root, created_at, last_opened_at) VALUES (?, ?, ?, ?, ?, ?);",
                (ws_id, path_hash, display_name, root_path, now, now)
            )
            return {
                "id": ws_id,
                "canonical_path_hash": path_hash,
                "display_name": display_name,
                "git_root": root_path,
                "created_at": now,
                "last_opened_at": now
            }

    def create_conversation(self, workspace_id: str, title: str = "New Conversation", parent_id: Optional[str] = None) -> Dict[str, Any]:
        conv_id = uuid.uuid4().hex
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT INTO conversations 
                (id, workspace_id, title, status, parent_conversation_id, created_at, updated_at) 
                VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?);""",
                (conv_id, workspace_id, title, parent_id, now, now)
            )
        return self.get_conversation(conv_id)

    def get_conversation(self, conv_id: str) -> Optional[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM conversations WHERE id = ?;", (conv_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def list_conversations(self, workspace_id: str, limit: int = 20, search: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            if search:
                term = f"%{search}%"
                cur.execute(
                    "SELECT * FROM conversations WHERE workspace_id = ? AND status != 'DELETED' AND (title LIKE ? OR compact_summary LIKE ?) ORDER BY updated_at DESC LIMIT ?;",
                    (workspace_id, term, term, limit)
                )
            else:
                cur.execute(
                    "SELECT * FROM conversations WHERE workspace_id = ? AND status != 'DELETED' ORDER BY updated_at DESC LIMIT ?;",
                    (workspace_id, limit)
                )
            return [dict(row) for row in cur.fetchall()]

    def update_conversation(self, conv_id: str, **kwargs) -> bool:
        if not kwargs:
            return False
        kwargs["updated_at"] = time.time()
        keys = ", ".join(f"{k} = ?" for k in kwargs.keys())
        values = list(kwargs.values()) + [conv_id]
        with self.db.get_connection() as conn:
            conn.execute(f"UPDATE conversations SET {keys} WHERE id = ?;", values)
        return True

    def save_message(self, conv_id: str, turn_id: str, role: str, content: str, token_count: int = 0) -> str:
        msg_id = uuid.uuid4().hex
        now = time.time()
        c_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
        with self.db.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO turns (id, conversation_id, ordinal, started_at) VALUES (?, ?, 1, ?);", (turn_id, conv_id, now))
            conn.execute(
                """INSERT INTO messages 
                (id, conversation_id, turn_id, role, content, created_at, token_count, content_hash) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
                (msg_id, conv_id, turn_id, role, content, now, token_count, c_hash)
            )
        return msg_id

    def get_messages_for_conversation(self, conv_id: str) -> List[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC;", (conv_id,))
            return [dict(row) for row in cur.fetchall()]
