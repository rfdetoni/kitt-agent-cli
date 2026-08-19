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
        d["allowed_paths"] = json.loads(d.pop("allowed_paths_json", "[]") or "[]")
        d["enabled_tools"] = json.loads(d.pop("enabled_tools_json", "[]") or "[]")
        try:
            d["capabilities"] = json.loads(d.pop("capabilities_json", "[]") or "[]")
        except Exception:
            d["capabilities"] = []
        return ChildSession(
            id=d["id"],
            parent_conversation_id=d["parent_conversation_id"],
            parent_turn_id=d["parent_turn_id"],
            name=d["name"],
            task=d["task"],
            state=d["state"],
            depth=d["depth"],
            model_profile=d["model_profile"],
            allowed_paths=d["allowed_paths"],
            enabled_tools=d["enabled_tools"],
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
            capabilities=d.get("capabilities", []),
            context_summary=d.get("context_summary", ""),
        )

    def create(
        self,
        parent_conversation_id: str,
        parent_turn_id: str,
        name: str,
        task: str,
        depth: int,
        model_profile: str,
        allowed_paths: List[str],
        enabled_tools: List[str],
        token_budget: int,
        timeout_seconds: float,
    ) -> ChildSession:
        cid = f"child_{uuid.uuid4().hex}"
        now = time.time()
        with self.db.get_connection() as c:
            c.execute(
                """
                INSERT INTO child_sessions (
                    id, parent_conversation_id, parent_turn_id, name, task, state, depth,
                    model_profile, allowed_paths_json, enabled_tools_json, token_budget,
                    timeout_seconds, created_at, capabilities_json
                )
                VALUES (?, ?, ?, ?, ?, 'CREATED', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cid,
                    parent_conversation_id,
                    parent_turn_id,
                    name,
                    task,
                    depth,
                    model_profile,
                    json.dumps(allowed_paths),
                    json.dumps(enabled_tools),
                    token_budget,
                    timeout_seconds,
                    now,
                    json.dumps(enabled_tools),
                ),
            )
            c.commit()
        return self.get(cid)

    def get(self, cid: str) -> Optional[ChildSession]:
        with self.db.get_connection() as c:
            r = c.execute("SELECT * FROM child_sessions WHERE id = ?", (cid,)).fetchone()
            return self._row(r) if r else None

    def list(self, parent_conversation_id: str, limit: int = 20) -> List[ChildSession]:
        with self.db.get_connection() as c:
            rows = c.execute(
                "SELECT * FROM child_sessions WHERE parent_conversation_id = ? ORDER BY created_at DESC LIMIT ?",
                (parent_conversation_id, min(max(limit, 1), 100)),
            ).fetchall()
            return [self._row(r) for r in rows]

    def update(self, cid: str, **fields) -> None:
        allowed = {
            "state", "tokens_used", "result_artifact_id", "error", "started_at",
            "completed_at", "task", "current_task_id", "task_started_at",
            "capabilities_json", "context_summary",
        }
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        with self.db.get_connection() as c:
            c.execute(
                "UPDATE child_sessions SET " + ", ".join(f"{k} = ?" for k in fields) + " WHERE id = ?",
                [*fields.values(), cid],
            )
            c.commit()
