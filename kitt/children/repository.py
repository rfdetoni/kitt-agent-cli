from __future__ import annotations

import json
import time
import uuid
from typing import List, Optional

from kitt.children.models import ChildSession
from kitt.history.database import HistoryDatabase


class ChildRepository:
    def __init__(self, db: HistoryDatabase):
        self.db = db

    @staticmethod
    def _row(r) -> ChildSession:
        d = dict(r)
        return ChildSession(
            id=d["id"],
            parent_conversation_id=d["parent_conversation_id"],
            parent_turn_id=d["parent_turn_id"],
            name=d["name"],
            task=d["task"],
            state=d["state"],
            depth=d["depth"],
            model_profile=d["model_profile"],
            allowed_paths=json.loads(d.get("allowed_paths_json") or "[]"),
            enabled_tools=json.loads(d.get("enabled_tools_json") or "[]"),
            token_budget=d.get("token_budget", 0),
            tokens_used=d.get("tokens_used", 0),
            timeout_seconds=d.get("timeout_seconds", 120),
            result_artifact_id=d.get("result_artifact_id"),
            error=d.get("error"),
            created_at=d.get("created_at", 0),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            current_task_id=d.get("current_task_id"),
            task_started_at=d.get("task_started_at"),
            capabilities=json.loads(d.get("capabilities_json") or "[]"),
            context_summary=d.get("context_summary", ""),
            runtime_conversation_id=d.get("runtime_conversation_id"),
            security_context=json.loads(d.get("security_context_json") or "{}"),
        )

    def create(self, parent_conversation_id, parent_turn_id, name, task, depth,
               model_profile, allowed_paths, enabled_tools, token_budget,
               timeout_seconds, capabilities=None, runtime_conversation_id=None,
               security_context=None):
        cid = f"child_{uuid.uuid4().hex}"
        runtime_conversation_id = runtime_conversation_id or f"childconv_{cid}"
        now = time.time()
        with self.db.get_connection() as c:
            parent = c.execute(
                "SELECT workspace_id FROM conversations WHERE id=?",
                (parent_conversation_id,),
            ).fetchone()
            if not parent:
                raise ValueError("Parent conversation does not exist")
            c.execute(
                """INSERT OR IGNORE INTO conversations(
                    id,workspace_id,title,status,parent_conversation_id,created_at,updated_at
                ) VALUES(?,?,?,'INTERNAL_CHILD',?,?,?)""",
                (
                    runtime_conversation_id,
                    parent["workspace_id"],
                    f"[child] {name}",
                    parent_conversation_id,
                    now,
                    now,
                ),
            )
            c.execute(
                """INSERT INTO child_sessions(
                    id,parent_conversation_id,parent_turn_id,name,task,state,depth,
                    model_profile,allowed_paths_json,enabled_tools_json,token_budget,
                    timeout_seconds,created_at,capabilities_json,runtime_conversation_id,
                    security_context_json
                ) VALUES(?,?,?,?,?,'CREATED',?,?,?,?,?,?,?,?,?,?)""",
                (
                    cid, parent_conversation_id, parent_turn_id, name, task, depth,
                    model_profile, json.dumps(allowed_paths), json.dumps(enabled_tools),
                    token_budget, timeout_seconds, now,
                    json.dumps(sorted(capabilities or ())), runtime_conversation_id,
                    json.dumps(security_context or {}, ensure_ascii=False),
                ),
            )
            c.commit()
        return self.get(cid)

    def get(self, cid: str) -> Optional[ChildSession]:
        with self.db.get_connection() as c:
            r = c.execute("SELECT * FROM child_sessions WHERE id=?", (cid,)).fetchone()
            return self._row(r) if r else None

    def get_scoped(self, cid: str, conversation_id=None, workspace_id=None):
        with self.db.get_connection() as c:
            row = c.execute(
                """SELECT cs.* FROM child_sessions cs
                   JOIN conversations p ON p.id=cs.parent_conversation_id
                   WHERE cs.id=?""", (cid,)
            ).fetchone()
            if not row:
                return None
            if conversation_id and row["parent_conversation_id"] != conversation_id:
                raise PermissionError("Cross-conversation child access blocked")
            if workspace_id:
                ws = c.execute(
                    "SELECT workspace_id FROM conversations WHERE id=?",
                    (row["parent_conversation_id"],),
                ).fetchone()
                if not ws or ws["workspace_id"] != workspace_id:
                    raise PermissionError("Cross-workspace child access blocked")
            return self._row(row)

    def list(self, parent_conversation_id: str, limit=20):
        with self.db.get_connection() as c:
            rows = c.execute(
                "SELECT * FROM child_sessions WHERE parent_conversation_id=? ORDER BY created_at DESC LIMIT ?",
                (parent_conversation_id, min(max(limit, 1), 100)),
            ).fetchall()
            return [self._row(r) for r in rows]

    def update(self, cid: str, **fields):
        allowed = {
            "state", "tokens_used", "result_artifact_id", "error", "started_at",
            "completed_at", "task", "current_task_id", "task_started_at",
            "capabilities_json", "context_summary", "runtime_conversation_id",
            "security_context_json",
        }
        values = {k: v for k, v in fields.items() if k in allowed}
        if not values:
            return
        with self.db.get_connection() as c:
            c.execute(
                "UPDATE child_sessions SET " + ", ".join(f"{k}=?" for k in values) + " WHERE id=?",
                [*values.values(), cid],
            )
            c.commit()
