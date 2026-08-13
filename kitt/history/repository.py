import json
import uuid
import time
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional
from kitt.history.database import HistoryDatabase

def json_dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def canonical_workspace_path(root_path: str | Path) -> str:
    return str(Path(root_path).expanduser().resolve(strict=False))

def _workspace_row(conn, path_hash: str) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM workspaces WHERE canonical_path_hash = ?;", (path_hash,))
    row = cur.fetchone()
    return dict(row) if row else None

def _create_workspace_row(conn, canon: str, path_hash: str) -> Dict[str, Any]:
    ws_id = uuid.uuid4().hex
    display_name = str(Path(canon).name) or "workspace"
    now = time.time()
    conn.execute(
        "INSERT INTO workspaces (id, canonical_path_hash, display_name, git_root, created_at, last_opened_at) VALUES (?, ?, ?, ?, ?, ?);",
        (ws_id, path_hash, display_name, canon, now, now)
    )
    return {
        "id": ws_id,
        "canonical_path_hash": path_hash,
        "display_name": display_name,
        "git_root": canon,
        "created_at": now,
        "last_opened_at": now
    }

def resolve_workspace_identity(db: HistoryDatabase, root_path: str | Path):
    """Resolve (or create) the persisted workspace identity for a canonical root."""
    from kitt.core.workspace_identity import WorkspaceIdentity

    canon = canonical_workspace_path(root_path)
    path_hash = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    with db.get_connection() as conn:
        row = _workspace_row(conn, path_hash)
        if row:
            conn.execute("UPDATE workspaces SET last_opened_at = ? WHERE id = ?;", (time.time(), row["id"]))
            ws = row
        else:
            ws = _create_workspace_row(conn, canon, path_hash)
    return WorkspaceIdentity(id=ws["id"], canonical_root=Path(canon), canonical_path_hash=ws["canonical_path_hash"])

def get_or_create_workspace_identity(root_path: str | Path):
    """Backward-compatible helper used by WorkspaceIdentity.build without a db.

    Uses a transient connection against the default on-disk location.  Prefer
    ``resolve_workspace_identity`` when a database handle already exists.
    """
    from kitt.history.database import HistoryDatabase
    from kitt.history.migrations import MigrationRunner

    canon = canonical_workspace_path(root_path)
    path_hash = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    import sqlite3
    db_path = Path(canon) / ".kitt" / "history" / "history.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path), timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        MigrationRunner().migrate(conn)
        conn.commit()
        row = _workspace_row(conn, path_hash)
        if row:
            conn.execute("UPDATE workspaces SET last_opened_at = ? WHERE id = ?;", (time.time(), row["id"]))
            ws = row
        else:
            ws = _create_workspace_row(conn, canon, path_hash)
    from kitt.core.workspace_identity import WorkspaceIdentity
    return WorkspaceIdentity(id=ws["id"], canonical_root=Path(canon), canonical_path_hash=ws["canonical_path_hash"])

class HistoryRepository:
    """Data access repository for SQLite history entities."""

    def __init__(self, db: HistoryDatabase):
        self.db = db

    def get_or_create_workspace(self, root_path: str) -> Dict[str, Any]:
        canon = canonical_workspace_path(root_path)
        path_hash = hashlib.sha256(canon.encode('utf-8')).hexdigest()
        display_name = str(Path(canon).name) or "workspace"
        now = time.time()

        with self.db.get_connection() as conn:
            row = _workspace_row(conn, path_hash)
            if row:
                conn.execute("UPDATE workspaces SET last_opened_at = ? WHERE id = ?;", (now, row["id"]))
                return row
            ws = _create_workspace_row(conn, canon, path_hash)
            return ws

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

    def fork_conversation(self, conv_id: str, new_title: Optional[str] = None) -> Optional[Dict[str, Any]]:
        orig_conv = self.get_conversation(conv_id)
        if not orig_conv:
            return None

        title = new_title or f"Fork of {orig_conv['title']}"
        new_conv = self.create_conversation(orig_conv["workspace_id"], title=title, parent_id=conv_id)
        new_conv_id = new_conv["id"]

        msgs = self.get_messages_for_conversation(conv_id)
        # Clone the materialized messages once, preserving order and turn ids.
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ordinal = 0
            for msg in msgs:
                ordinal += 1
                new_turn_id = f"fork_{new_conv_id[:8]}_{ordinal}"
                new_msg_id = uuid.uuid4().hex
                now = time.time()
                conn.execute(
                    "INSERT INTO turns (id, conversation_id, ordinal, started_at) VALUES (?, ?, ?, ?);",
                    (new_turn_id, new_conv_id, ordinal, now)
                )
                conn.execute(
                    """INSERT INTO messages (id, conversation_id, turn_id, role, content, created_at, token_count, content_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
                    (new_msg_id, new_conv_id, new_turn_id, msg["role"], msg["content"],
                     now, msg.get("token_count", 0), hashlib.sha256(msg["content"].encode("utf-8")).hexdigest())
                )
                conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?;", (now, new_conv_id))
        return new_conv

    def list_conversations(self, workspace_id: str, limit: int = 20, offset: int = 0, search: Optional[str] = None) -> List[Dict[str, Any]]:
        MAX_PAGE_SIZE = 100
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)
        
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            if search:
                term = f"%{search}%"
                cur.execute(
                    "SELECT * FROM conversations WHERE workspace_id = ? AND status != 'DELETED' AND (title LIKE ? OR compact_summary LIKE ?) ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?;",
                    (workspace_id, term, term, limit, offset)
                )
            else:
                cur.execute(
                    "SELECT * FROM conversations WHERE workspace_id = ? AND status != 'DELETED' ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?;",
                    (workspace_id, limit, offset)
                )
            return [dict(row) for row in cur.fetchall()]

    def delete_conversation(self, conv_id: str) -> bool:
        with self.db.get_connection() as conn:
            cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
            return cur.rowcount > 0

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
        import re
        msg_id = uuid.uuid4().hex
        now = time.time()

        # Redaction: Replace typical API keys / secrets before persisting
        content = re.sub(r'(?i)(bearer\s+|api_key[\s=:]+|secret[\s=:]+)[A-Za-z0-9_\-\.]{15,}', r'\1[REDACTED]', content)

        c_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id FROM turns WHERE id = ?", (turn_id,)).fetchone()
            if not row:
                ordinal = conn.execute(
                    "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM turns WHERE conversation_id = ?",
                    (conv_id,)
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO turns (id, conversation_id, ordinal, started_at) VALUES (?, ?, ?, ?);",
                    (turn_id, conv_id, ordinal, now)
                )
            conn.execute(
                """INSERT INTO messages 
                (id, conversation_id, turn_id, role, content, created_at, token_count, content_hash) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
                (msg_id, conv_id, turn_id, role, content, now, token_count, c_hash)
            )
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?;", (now, conv_id))
            entry_type = ("USER_MESSAGE" if role == "user"
                          else ("ASSISTANT_MESSAGE" if role == "assistant" else "TOOL_RESULT"))
            payload = {"role": role, "content": content, "message_id": msg_id}
            payload_json = json_dumps(payload)
            entry_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            entry_id = f"entry_{uuid.uuid4().hex}"
            parent = conn.execute(
                "SELECT active_entry_id FROM conversations WHERE id = ?", (conv_id,)
            ).fetchone()
            parent_entry_id = parent["active_entry_id"] if parent else None
            generation = 0
            if parent_entry_id:
                gen = conn.execute(
                    "SELECT generation FROM session_entries WHERE id = ?", (parent_entry_id,)
                ).fetchone()
                generation = (gen["generation"] + 1) if gen else 0
            conn.execute(
                """INSERT INTO session_entries
                (id, conversation_id, parent_entry_id, turn_id, entry_type, payload_json,
                 include_in_context, generation, created_at, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (entry_id, conv_id, parent_entry_id, turn_id, entry_type,
                 payload_json, generation, now, entry_hash),
            )
            conn.execute(
                """UPDATE conversations SET active_entry_id = ?, active_generation = ?,
                   updated_at = ? WHERE id = ?""",
                (entry_id, generation, now, conv_id),
            )
        return msg_id
        
    def save_telemetry(self, conv_id: str, turn_id: str, route: str, start_time: float, duration_ms: float, input_tokens: int, output_tokens: int, tokens_saved: int) -> str:
        telemetry_id = uuid.uuid4().hex
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO telemetry_events 
                (id, conversation_id, turn_id, route, start_time, duration_ms, input_tokens, output_tokens, tokens_saved) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                (telemetry_id, conv_id, turn_id, route, start_time, duration_ms, input_tokens, output_tokens, tokens_saved)
            )
        return telemetry_id

    def get_telemetry_stats(self, conv_id: Optional[str] = None) -> Dict[str, Any]:
        query = "SELECT COUNT(*) as count, SUM(input_tokens) as input, SUM(output_tokens) as output, SUM(tokens_saved) as saved, SUM(duration_ms) as duration FROM telemetry_events"
        args = []
        if conv_id:
            query += " WHERE conversation_id = ?"
            args = [conv_id]
        
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, tuple(args))
            row = cur.fetchone()
            if not row or row["count"] == 0:
                return {"count": 0, "input": 0, "output": 0, "saved": 0, "duration": 0.0}
            return dict(row)

    def get_messages_for_conversation(self, conv_id: str) -> List[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC;", (conv_id,))
            return [dict(row) for row in cur.fetchall()]

    def save_pending_action(self, pa: 'PendingAction'):
        import json
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT INTO pending_actions
                (id, approval_request_id, turn_id, conversation_id, workspace_id, tool_name, 
                 normalized_args_json, action_hash, source_response_sha256, affected_paths_json, 
                 before_hashes_json, created_at, expires_at, state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pa.id, pa.approval_request_id, pa.turn_id, pa.conversation_id, pa.workspace_id, pa.tool_name,
                 json.dumps(pa.normalized_args), pa.action_hash, pa.source_response_sha256, 
                 json.dumps(pa.affected_paths), json.dumps(pa.before_hashes), pa.created_at, pa.expires_at, pa.state)
            )

    def get_valid_pending_action(self, action_id: str, workspace_id: str) -> Optional['PendingAction']:
        import json, time
        from kitt.core.pending_action import PendingAction
        
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM pending_actions WHERE id = ? AND workspace_id = ? AND state = 'pending';", 
                (action_id, workspace_id)
            )
            row = cur.fetchone()
            if not row:
                return None
                
            if time.time() > row["expires_at"]:
                conn.execute("UPDATE pending_actions SET state = 'expired' WHERE id = ?;", (action_id,))
                return None
                
            return PendingAction(
                id=row["id"],
                approval_request_id=row["approval_request_id"],
                turn_id=row["turn_id"],
                conversation_id=row["conversation_id"],
                workspace_id=row["workspace_id"],
                tool_name=row["tool_name"],
                normalized_args=json.loads(row["normalized_args_json"]),
                action_hash=row["action_hash"],
                source_response_sha256=row["source_response_sha256"],
                affected_paths=json.loads(row["affected_paths_json"]),
                before_hashes=json.loads(row["before_hashes_json"]),
                created_at=row["created_at"],
                expires_at=row["expires_at"],
                state=row["state"]
            )
            
    def consume_pending_action(self, action_id: str) -> bool:
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE pending_actions SET state = 'consumed' WHERE id = ? AND state = 'pending';", (action_id,))
            return cur.rowcount > 0

    def cancel_pending_action(self, action_id: str) -> bool:
        with self.db.get_connection() as conn:
            cur = conn.execute(
                "UPDATE pending_actions SET state='cancelled' WHERE id=? AND state='pending'",
                (action_id,),
            )
            return cur.rowcount > 0
