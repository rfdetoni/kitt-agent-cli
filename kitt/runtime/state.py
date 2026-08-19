from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional
from kitt.history.database import HistoryDatabase


class RuntimeStateStore:
    """Persistent, session-scoped key-value state store backed by SQLite with strict bounds."""

    MAX_ENTRIES_PER_SESSION = 100
    MAX_ENTRY_BYTES = 64 * 1024       # 64 KB
    MAX_TOTAL_BYTES = 512 * 1024      # 512 KB

    def __init__(self, db: HistoryDatabase, workspace_id: str, conversation_id: str):
        self.db = db
        self.workspace_id = workspace_id
        self.conversation_id = conversation_id

    def _cleanup_expired(self, conn) -> None:
        now = time.time()
        conn.execute(
            "DELETE FROM runtime_states WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now,)
        )

    def get(self, key: str) -> Optional[Any]:
        """Retrieve a value by key if it exists and is not expired."""
        if not key or not isinstance(key, str):
            return None
        with self.db.get_connection() as conn:
            self._cleanup_expired(conn)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT value_json, expires_at FROM runtime_states
                WHERE workspace_id = ? AND conversation_id = ? AND state_key = ?
                """,
                (self.workspace_id, self.conversation_id, key)
            )
            row = cur.fetchone()
            if not row:
                return None
            val_json, expires_at = row[0], row[1]
            if expires_at is not None and expires_at < time.time():
                return None
            try:
                return json.loads(val_json)
            except Exception:
                return None

    def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> bool:
        """Store a key-value pair under session scope with bounded limits."""
        if not key or not isinstance(key, str):
            raise ValueError("State key must be a non-empty string")
        if len(key) > 128:
            raise ValueError("State key exceeds maximum length of 128 characters")

        val_json = json.dumps(value, ensure_ascii=False)
        val_bytes = len(val_json.encode("utf-8"))
        if val_bytes > self.MAX_ENTRY_BYTES:
            raise ValueError(f"State value size ({val_bytes} bytes) exceeds limit ({self.MAX_ENTRY_BYTES} bytes)")

        now = time.time()
        expires_at = (now + ttl_seconds) if ttl_seconds is not None else None

        with self.db.get_connection() as conn:
            self._cleanup_expired(conn)
            cur = conn.cursor()

            # Check total count
            cur.execute(
                "SELECT COUNT(*), COALESCE(SUM(bytes_count), 0) FROM runtime_states WHERE workspace_id = ? AND conversation_id = ?",
                (self.workspace_id, self.conversation_id)
            )
            row = cur.fetchone()
            count, total_bytes = (row[0], row[1]) if row else (0, 0)

            # Check if key already exists
            cur.execute(
                "SELECT bytes_count FROM runtime_states WHERE workspace_id = ? AND conversation_id = ? AND state_key = ?",
                (self.workspace_id, self.conversation_id, key)
            )
            existing = cur.fetchone()
            if existing:
                total_bytes -= existing[0]
            else:
                if count >= self.MAX_ENTRIES_PER_SESSION:
                    raise ValueError(f"Runtime state entry limit reached ({self.MAX_ENTRIES_PER_SESSION})")

            if total_bytes + val_bytes > self.MAX_TOTAL_BYTES:
                raise ValueError(f"Runtime state total byte limit ({self.MAX_TOTAL_BYTES} bytes) would be exceeded")

            state_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO runtime_states (id, workspace_id, conversation_id, state_key, value_json, bytes_count, ttl_seconds, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, conversation_id, state_key) DO UPDATE SET
                    value_json = excluded.value_json,
                    bytes_count = excluded.bytes_count,
                    ttl_seconds = excluded.ttl_seconds,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at
                """,
                (state_id, self.workspace_id, self.conversation_id, key, val_json, val_bytes, ttl_seconds, now, now, expires_at)
            )
            conn.commit()
            return True

    def delete(self, key: str) -> bool:
        """Delete a key."""
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM runtime_states WHERE workspace_id = ? AND conversation_id = ? AND state_key = ?",
                (self.workspace_id, self.conversation_id, key)
            )
            conn.commit()
            return cur.rowcount > 0

    def list_keys(self) -> List[Dict[str, Any]]:
        """List all active keys and metadata for this conversation."""
        with self.db.get_connection() as conn:
            self._cleanup_expired(conn)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT state_key, bytes_count, created_at, updated_at, expires_at
                FROM runtime_states
                WHERE workspace_id = ? AND conversation_id = ?
                ORDER BY updated_at DESC
                """,
                (self.workspace_id, self.conversation_id)
            )
            rows = cur.fetchall()
            return [
                {
                    "key": r[0],
                    "bytes": r[1],
                    "created_at": r[2],
                    "updated_at": r[3],
                    "expires_at": r[4],
                }
                for r in rows
            ]

    def clear(self) -> int:
        """Clear all entries for this conversation."""
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM runtime_states WHERE workspace_id = ? AND conversation_id = ?",
                (self.workspace_id, self.conversation_id)
            )
            conn.commit()
            return cur.rowcount
