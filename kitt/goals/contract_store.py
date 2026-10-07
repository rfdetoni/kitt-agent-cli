from __future__ import annotations

import json
import time
import uuid
from typing import Any, Iterable

from kitt.goals.models import ContractItem
from kitt.history.database import HistoryDatabase


class ContractLeaseError(RuntimeError):
    """Raised when contract state is mutated without the current goal lease."""


class ContractItemExhausted(RuntimeError):
    """Raised when the current contract item cannot consume another attempt."""


class ContractStore:
    """Durable task-contract state owned by the existing Goals scheduler."""

    def __init__(self, db: HistoryDatabase):
        self.db = db

    @staticmethod
    def _decode_list(raw: Any) -> list[str]:
        try:
            value = json.loads(raw or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            value = []
        return [str(item) for item in value] if isinstance(value, list) else []

    @staticmethod
    def _decode_evidence(raw: Any) -> dict[str, Any]:
        try:
            value = json.loads(raw or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            value = {}
        return dict(value) if isinstance(value, dict) else {}

    @classmethod
    def _item(cls, row) -> ContractItem:
        data = dict(row)
        return ContractItem(
            id=data["id"],
            goal_id=data["goal_id"],
            position=int(data["position"]),
            local_id=data["local_id"],
            kind=data["kind"],
            title=data["title"],
            prompt=data["prompt"],
            validation_prompt=data["validation_prompt"],
            criteria=cls._decode_list(data.get("criteria_json")),
            check_ids=cls._decode_list(data.get("check_ids_json")),
            paths=cls._decode_list(data.get("paths_json")),
            depends_on=cls._decode_list(data.get("depends_on_json")),
            status=data["status"],
            attempts=int(data.get("attempts", 0) or 0),
            max_attempts=int(data.get("max_attempts", 5) or 5),
            last_feedback=data.get("last_feedback"),
            evidence=cls._decode_evidence(data.get("evidence_json")),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
        )

    def insert_items(
        self,
        connection,
        goal_id: str,
        items: Iterable[dict[str, Any]],
        *,
        max_attempts: int,
    ) -> None:
        now = time.time()
        for position, item in enumerate(items):
            connection.execute(
                """INSERT INTO goal_contract_items(
                    id,goal_id,position,local_id,kind,title,prompt,validation_prompt,
                    criteria_json,check_ids_json,paths_json,depends_on_json,status,
                    attempts,max_attempts,last_feedback,evidence_json,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'PENDING',0,?,NULL,'{}',?)""",
                (
                    f"contract_item_{uuid.uuid4().hex}",
                    goal_id,
                    position,
                    str(item["local_id"]),
                    str(item.get("kind") or "task"),
                    str(item["title"]),
                    str(item["prompt"]),
                    str(item["validation_prompt"]),
                    json.dumps(list(item.get("success_criteria") or []), ensure_ascii=False),
                    json.dumps(list(item.get("check_ids") or []), ensure_ascii=False),
                    json.dumps(list(item.get("paths") or []), ensure_ascii=False),
                    json.dumps(list(item.get("depends_on") or []), ensure_ascii=False),
                    max(1, int(max_attempts)),
                    now,
                ),
            )

    def items(self, goal_id: str, connection=None) -> list[ContractItem]:
        if connection is None:
            with self.db.get_connection() as conn:
                return self.items(goal_id, conn)
        rows = connection.execute(
            "SELECT * FROM goal_contract_items WHERE goal_id=? ORDER BY position ASC",
            (goal_id,),
        ).fetchall()
        return [self._item(row) for row in rows]

    def current(self, goal_id: str, connection=None) -> ContractItem | None:
        if connection is None:
            with self.db.get_connection() as conn:
                return self.current(goal_id, conn)
        items = self.items(goal_id, connection)
        first = next((item for item in items if item.status != "DONE"), None)
        if first is None:
            return None
        done_ids = {item.local_id for item in items if item.status == "DONE"}
        if not set(first.depends_on).issubset(done_ids):
            missing = sorted(set(first.depends_on) - done_ids)
            raise RuntimeError(
                f"Contract item {first.local_id} has unfinished dependencies: {missing}"
            )
        return first

    def is_complete(self, goal_id: str, connection=None) -> bool:
        if connection is None:
            with self.db.get_connection() as conn:
                return self.is_complete(goal_id, conn)
        row = connection.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END) AS done
               FROM goal_contract_items WHERE goal_id=?""",
            (goal_id,),
        ).fetchone()
        total = int(row["total"] or 0)
        done = int(row["done"] or 0)
        return total == 0 or total == done

    def begin_attempt(
        self,
        goal_id: str,
        *,
        lease_id: str,
        lease_owner_id: str,
    ) -> ContractItem | None:
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            lease = conn.execute(
                """SELECT 1 FROM goals
                   WHERE id=? AND state='RUNNING' AND lease_id=? AND lease_owner_id=?
                   AND lease_expires_at>?""",
                (goal_id, lease_id, lease_owner_id, now),
            ).fetchone()
            if lease is None:
                raise ContractLeaseError("Contract attempt requires the current goal lease")

            item = self.current(goal_id, conn)
            if item is None:
                return None
            if item.status == "BLOCKED" or item.attempts >= item.max_attempts:
                conn.execute(
                    """UPDATE goal_contract_items
                       SET status='BLOCKED',updated_at=? WHERE id=?""",
                    (now, item.id),
                )
                raise ContractItemExhausted(
                    f"Contract item {item.local_id} exhausted after {item.attempts} attempts"
                )

            conn.execute(
                """UPDATE goal_contract_items
                   SET status='RUNNING',attempts=attempts+1,updated_at=?
                   WHERE id=?""",
                (now, item.id),
            )
            row = conn.execute(
                "SELECT * FROM goal_contract_items WHERE id=?",
                (item.id,),
            ).fetchone()
            return self._item(row)

    def commit_outcome(
        self,
        goal_id: str,
        item_id: str,
        *,
        lease_id: str,
        lease_owner_id: str,
        outcome: str,
        feedback: str = "",
        evidence: dict[str, Any] | None = None,
        next_run: float | None = None,
    ) -> dict[str, Any]:
        outcome = str(outcome or "").upper()
        if outcome not in {"DONE", "RETRY", "BLOCKED", "WAITING_APPROVAL"}:
            raise ValueError(f"Unsupported contract outcome: {outcome}")

        now = time.time()
        serialized_evidence = json.dumps(evidence or {}, ensure_ascii=False)
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            goal = conn.execute(
                """SELECT id,state,lease_id,lease_owner_id,lease_expires_at
                   FROM goals WHERE id=?""",
                (goal_id,),
            ).fetchone()
            if (
                goal is None
                or goal["state"] != "RUNNING"
                or goal["lease_id"] != lease_id
                or goal["lease_owner_id"] != lease_owner_id
                or float(goal["lease_expires_at"] or 0) <= now
            ):
                raise ContractLeaseError("Contract outcome rejected after goal lease loss")

            row = conn.execute(
                "SELECT * FROM goal_contract_items WHERE id=? AND goal_id=?",
                (item_id, goal_id),
            ).fetchone()
            if row is None:
                raise ValueError("Contract item not found")
            item = self._item(row)
            if item.status != "RUNNING":
                raise ValueError(
                    f"Contract item {item.local_id} is not RUNNING: {item.status}"
                )

            item_status = item.status
            goal_state = "ACTIVE"
            error = feedback or None
            completed_at = None

            if outcome == "DONE":
                item_status = "DONE"
                conn.execute(
                    """UPDATE goal_contract_items
                       SET status='DONE',last_feedback=NULL,evidence_json=?,updated_at=?
                       WHERE id=?""",
                    (serialized_evidence, now, item.id),
                )
                complete = self.is_complete(goal_id, conn)
                if complete:
                    goal_state = "SUCCEEDED"
                    next_run = None
                    error = None
                    completed_at = now
            elif outcome == "WAITING_APPROVAL":
                item_status = "PENDING"
                goal_state = "WAITING_APPROVAL"
                next_run = None
                conn.execute(
                    """UPDATE goal_contract_items
                       SET status='PENDING',last_feedback=?,evidence_json=?,updated_at=?
                       WHERE id=?""",
                    (feedback or None, serialized_evidence, now, item.id),
                )
            else:
                exhausted = outcome == "BLOCKED" or item.attempts >= item.max_attempts
                item_status = "BLOCKED" if exhausted else "PENDING"
                goal_state = "FAILED" if exhausted else "ACTIVE"
                if exhausted:
                    next_run = None
                    error = (
                        feedback
                        or f"Contract item {item.local_id} exhausted after {item.attempts} attempts"
                    )
                conn.execute(
                    """UPDATE goal_contract_items
                       SET status=?,last_feedback=?,evidence_json=?,updated_at=?
                       WHERE id=?""",
                    (item_status, feedback or None, serialized_evidence, now, item.id),
                )

            cursor = conn.execute(
                """UPDATE goals
                   SET state=?,next_run_at=?,last_error=?,completed_at=?,updated_at=?,
                       lease_id=NULL,lease_owner_id=NULL,lease_expires_at=NULL,
                       lease_heartbeat_at=NULL
                   WHERE id=? AND lease_id=? AND lease_owner_id=?""",
                (
                    goal_state,
                    next_run,
                    error,
                    completed_at,
                    now,
                    goal_id,
                    lease_id,
                    lease_owner_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ContractLeaseError("Contract outcome lost the goal lease before commit")
            return {
                "goal_state": goal_state,
                "item_status": item_status,
                "item_id": item.id,
                "local_id": item.local_id,
            }

    def cancel(
        self,
        goal_id: str,
        reason: str,
        *,
        conversation_id: str | None = None,
    ) -> bool:
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            where = "id=?"
            args: list[Any] = [goal_id]
            if conversation_id:
                where += " AND conversation_id=?"
                args.append(conversation_id)
            goal = conn.execute(
                f"SELECT * FROM goals WHERE {where}",
                args,
            ).fetchone()
            if goal is None:
                return False
            if str(goal["state"] or "").upper() in {
                "SUCCEEDED",
                "FAILED",
                "CANCELLED",
            }:
                return False
            item = self.current(goal_id, conn)
            if item is not None and item.status != "DONE":
                conn.execute(
                    """UPDATE goal_contract_items
                       SET status='BLOCKED',last_feedback=?,updated_at=?
                       WHERE id=?""",
                    (str(reason or "Cancelled"), now, item.id),
                )
            conn.execute(
                """UPDATE goals
                   SET state='CANCELLED',last_error=?,completed_at=?,next_run_at=NULL,
                       lease_id=NULL,lease_owner_id=NULL,lease_expires_at=NULL,
                       lease_heartbeat_at=NULL,updated_at=?
                   WHERE id=?""",
                (str(reason or "Cancelled"), now, now, goal_id),
            )
            return True

    def resume_after_approval(
        self,
        goal_id: str,
        *,
        conversation_id: str | None = None,
    ) -> ContractItem | None:
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            where = "id=? AND state='WAITING_APPROVAL' AND lease_id IS NULL"
            args: list[Any] = [goal_id]
            if conversation_id:
                where += " AND conversation_id=?"
                args.append(conversation_id)
            goal = conn.execute(
                f"SELECT * FROM goals WHERE {where}",
                args,
            ).fetchone()
            if goal is None:
                return None
            item = self.current(goal_id, conn)
            if item is None or item.status != "PENDING":
                return None
            conn.execute(
                """UPDATE goals
                   SET state='ACTIVE',last_error=NULL,next_run_at=?,updated_at=?
                   WHERE id=?""",
                (now, now, goal_id),
            )
            return item

    def block_waiting(
        self,
        goal_id: str,
        feedback: str,
        *,
        conversation_id: str | None = None,
    ) -> ContractItem | None:
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            where = "id=? AND state='WAITING_APPROVAL' AND lease_id IS NULL"
            args: list[Any] = [goal_id]
            if conversation_id:
                where += " AND conversation_id=?"
                args.append(conversation_id)
            goal = conn.execute(
                f"SELECT * FROM goals WHERE {where}",
                args,
            ).fetchone()
            if goal is None:
                return None
            item = self.current(goal_id, conn)
            if item is None:
                return None
            conn.execute(
                """UPDATE goal_contract_items
                   SET status='BLOCKED',last_feedback=?,updated_at=?
                   WHERE id=?""",
                (str(feedback or "Approval denied"), now, item.id),
            )
            conn.execute(
                """UPDATE goals
                   SET state='FAILED',last_error=?,completed_at=?,next_run_at=NULL,
                       updated_at=?
                   WHERE id=?""",
                (str(feedback or "Approval denied"), now, now, goal_id),
            )
            row = conn.execute(
                "SELECT * FROM goal_contract_items WHERE id=?",
                (item.id,),
            ).fetchone()
            return self._item(row)

    def resume(self, goal_id: str, *, conversation_id: str | None = None) -> ContractItem | None:
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            where = "id=?"
            args: list[Any] = [goal_id]
            if conversation_id:
                where += " AND conversation_id=?"
                args.append(conversation_id)
            goal = conn.execute(f"SELECT * FROM goals WHERE {where}", args).fetchone()
            if goal is None:
                return None
            if str(goal["state"] or "").upper() not in {
                "FAILED",
                "PAUSED",
                "PAUSED_BUDGET_EXCEEDED",
                "WAITING_APPROVAL",
            }:
                raise ValueError(
                    f"Contract goal cannot be resumed from state {goal['state']}"
                )
            if goal["lease_id"] is not None:
                raise ContractLeaseError("Contract cannot be resumed while a lease is active")
            item = self.current(goal_id, conn)
            if item is None:
                raise ValueError("Completed contract cannot be resumed")
            if item.status == "BLOCKED":
                conn.execute(
                    """UPDATE goal_contract_items
                       SET status='PENDING',attempts=0,last_feedback=NULL,updated_at=?
                       WHERE id=?""",
                    (now, item.id),
                )
            conn.execute(
                """UPDATE goals
                   SET state='ACTIVE',last_error=NULL,completed_at=NULL,next_run_at=?,
                       lease_id=NULL,lease_owner_id=NULL,lease_expires_at=NULL,
                       lease_heartbeat_at=NULL,updated_at=?
                   WHERE id=?""",
                (now, now, goal_id),
            )
            row = conn.execute(
                "SELECT * FROM goal_contract_items WHERE id=?",
                (item.id,),
            ).fetchone()
            return self._item(row)
