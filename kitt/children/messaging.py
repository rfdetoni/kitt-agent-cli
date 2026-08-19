from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from kitt.history.database import HistoryDatabase


@dataclass(frozen=True)
class ChildMessage:
    id: str
    conversation_id: str
    parent_id: str
    child_id: str
    sender_id: str
    recipient_id: str
    kind: str
    payload: Dict[str, Any]
    status: str
    timestamp: float


class ChildMessageRepository:
    """SQLite-backed message bus for parent-child and retained agent messaging."""

    def __init__(self, db: HistoryDatabase):
        self.db = db

    def send(
        self,
        conversation_id: str,
        parent_id: str,
        child_id: str,
        sender_id: str,
        recipient_id: str,
        payload: Dict[str, Any],
        kind: str = "DIRECT",
    ) -> ChildMessage:
        msg_id = f"msg_{uuid.uuid4().hex}"
        now = time.time()
        payload_json = json.dumps(payload, ensure_ascii=False)

        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO child_messages (id, conversation_id, parent_id, child_id, sender_id, recipient_id, kind, payload_json, status, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'SENT', ?)
                """,
                (msg_id, conversation_id, parent_id, child_id, sender_id, recipient_id, kind, payload_json, now),
            )
            conn.commit()

        return ChildMessage(
            id=msg_id,
            conversation_id=conversation_id,
            parent_id=parent_id,
            child_id=child_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            kind=kind,
            payload=payload,
            status="SENT",
            timestamp=now,
        )

    def list_messages(
        self,
        conversation_id: str,
        child_id: Optional[str] = None,
        recipient_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[ChildMessage]:
        query = "SELECT * FROM child_messages WHERE conversation_id = ?"
        params: List[Any] = [conversation_id]

        if child_id:
            query += " AND child_id = ?"
            params.append(child_id)
        if recipient_id:
            query += " AND recipient_id = ?"
            params.append(recipient_id)

        query += " ORDER BY timestamp ASC LIMIT ?"
        params.append(min(max(limit, 1), 200))

        with self.db.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            messages = []
            for r in rows:
                d = dict(r)
                try:
                    payload = json.loads(d.get("payload_json", "{}"))
                except Exception:
                    payload = {}
                messages.append(
                    ChildMessage(
                        id=d["id"],
                        conversation_id=d["conversation_id"],
                        parent_id=d["parent_id"],
                        child_id=d["child_id"],
                        sender_id=d["sender_id"],
                        recipient_id=d["recipient_id"],
                        kind=d["kind"],
                        payload=payload,
                        status=d["status"],
                        timestamp=d["timestamp"],
                    )
                )
            return messages

    def mark_delivered(self, message_id: str) -> bool:
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE child_messages SET status = 'DELIVERED' WHERE id = ?", (message_id,))
            conn.commit()
            return cur.rowcount > 0
