import hashlib
import time
import uuid
from typing import List, Optional
from kitt.history.database import HistoryDatabase
from kitt.queueing.models import QueuedInput

class InputQueueRepository:
    def __init__(self, db: HistoryDatabase):
        self.db = db

    @staticmethod
    def _row(r): return QueuedInput(**dict(r))

    def enqueue(self, conversation_id: str, kind: str, content: str,
                target_generation: int = 0) -> QueuedInput:
        if kind not in {"STEERING","FOLLOW_UP"}: raise ValueError("Invalid queue kind")
        content = content.strip()
        if not content or len(content) > 32_000: raise ValueError("Invalid queue content")
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            pos = conn.execute(
                "SELECT COALESCE(MAX(position),0)+1 FROM queued_inputs WHERE conversation_id=? AND kind=?",
                (conversation_id,kind)).fetchone()[0]
            qid=f"qi_{uuid.uuid4().hex}"; now=time.time()
            conn.execute("""INSERT INTO queued_inputs
                (id,conversation_id,kind,content,position,status,target_generation,created_at,content_hash)
                VALUES(?,?,?,?,?,'PENDING',?,?,?)""",
                (qid,conversation_id,kind,content,pos,target_generation,now,
                 hashlib.sha256(content.encode()).hexdigest()))
        return self.get(qid)

    def get(self, qid: str) -> Optional[QueuedInput]:
        with self.db.get_connection() as conn:
            r=conn.execute("SELECT * FROM queued_inputs WHERE id=?",(qid,)).fetchone()
            return self._row(r) if r else None

    def pending(self, conversation_id: str, kind: Optional[str]=None,
                limit: int=20) -> List[QueuedInput]:
        sql="SELECT * FROM queued_inputs WHERE conversation_id=? AND status='PENDING'"
        args=[conversation_id]
        if kind: sql += " AND kind=?"; args.append(kind)
        sql += " ORDER BY CASE kind WHEN 'STEERING' THEN 0 ELSE 1 END,position LIMIT ?"
        args.append(min(max(limit,1),100))
        with self.db.get_connection() as conn:
            return [self._row(r) for r in conn.execute(sql,args).fetchall()]

    def deliver(self, qid: str) -> bool:
        with self.db.get_connection() as conn:
            cur=conn.execute("UPDATE queued_inputs SET status='DELIVERED',delivered_at=? WHERE id=? AND status='PENDING'",(time.time(),qid))
            return cur.rowcount == 1

    def cancel(self, qid: str) -> bool:
        with self.db.get_connection() as conn:
            cur=conn.execute("UPDATE queued_inputs SET status='CANCELLED' WHERE id=? AND status='PENDING'",(qid,))
            return cur.rowcount == 1
