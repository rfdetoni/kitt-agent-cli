import json
import sqlite3
import time
import uuid
from typing import List, Optional

from kitt.goals.models import Goal, QualityGate
from kitt.history.database import HistoryDatabase
from kitt.security.capabilities import (
    CAP_ARTIFACT_READ, CAP_REPO_READ, CAP_REPO_SEARCH, canonicalize_capabilities,
)


class GoalService:
    TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "BUDGET_EXHAUSTED"}

    def __init__(self, db: HistoryDatabase):
        self.db = db

    def _get_gates(self, conn, goal_id):
        rows = conn.execute("SELECT * FROM quality_gates WHERE goal_id=?", (goal_id,)).fetchall()
        return [
            QualityGate(
                id=r["id"], goal_id=r["goal_id"], name=r["name"] or "QualityGate",
                argv=json.loads(r["argv_json"] or "[]"), status=r["status"],
                timeout_seconds=r["timeout_seconds"] or 120,
                last_exit_code=r["last_exit_code"],
                last_output_artifact_id=r["last_output_artifact_id"],
            ) for r in rows
        ]

    def _goal(self, r, conn=None):
        d = dict(r)
        if conn is None:
            with self.db.get_connection() as c:
                return self._goal(r, c)
        return Goal(
            id=d["id"], conversation_id=d["conversation_id"], objective=d["objective"],
            state=d["state"], token_budget=d.get("token_budget"),
            max_turns=d.get("max_turns", 12), max_wall_seconds=d.get("max_wall_seconds", 1800),
            tokens_used=d.get("tokens_used", 0), turns_used=d.get("turns_used", 0),
            continuations_used=d.get("continuations_used", 0),
            success_criteria=json.loads(d.get("success_criteria_json") or "[]"),
            started_at=d.get("started_at", 0), updated_at=d.get("updated_at", 0),
            completed_at=d.get("completed_at"), last_error=d.get("last_error"),
            gates=self._get_gates(conn, d["id"]), scheduled_at=d.get("scheduled_at"),
            next_run_at=d.get("next_run_at"), recurrence=d.get("recurrence"),
            heartbeat_enabled=bool(d.get("heartbeat_enabled", 0)),
            resume_policy=d.get("resume_policy", "manual"),
            owner_session_id=d.get("owner_session_id"), lease_id=d.get("lease_id"),
            lease_expires_at=d.get("lease_expires_at"),
            lease_owner_id=d.get("lease_owner_id"), lease_heartbeat_at=d.get("lease_heartbeat_at"),
            max_cost=float(d.get("max_cost", 0.0) or 0.0),
            cost_used=float(d.get("cost_used", 0.0) or 0.0),
            max_failures=d.get("max_failures", 5), max_retries=d.get("max_retries", 3),
            max_children=d.get("max_children", 5), failures_used=d.get("failures_used", 0),
            retries_used=d.get("retries_used", 0), children_used=d.get("children_used", 0),
            capabilities=json.loads(d.get("capabilities_json") or "[]"),
        )

    def create(self, conversation_id, objective, success_criteria=None, token_budget=None,
               max_turns=12, max_wall_seconds=1800, capabilities=None):
        if not objective.strip():
            raise ValueError("Goal objective required")
        caps = capabilities or [CAP_REPO_READ, CAP_REPO_SEARCH, CAP_ARTIFACT_READ]
        caps = sorted(canonicalize_capabilities(caps))
        gid, now = f"goal_{uuid.uuid4().hex}", time.time()
        with self.db.get_connection() as c:
            c.execute(
                """INSERT INTO goals(
                    id,conversation_id,objective,state,token_budget,max_turns,max_wall_seconds,
                    success_criteria_json,started_at,updated_at,capabilities_json
                ) VALUES(?,?,?,'ACTIVE',?,?,?,?,?,?,?)""",
                (gid, conversation_id, objective.strip(), token_budget, max(1, max_turns),
                 max(1, max_wall_seconds), json.dumps(success_criteria or []), now, now,
                 json.dumps(caps)),
            )
        return self.get(gid)

    def get(self, gid):
        with self.db.get_connection() as c:
            r = c.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
            return self._goal(r, c) if r else None

    def get_scoped(self, gid, conversation_id):
        with self.db.get_connection() as c:
            r = c.execute("SELECT * FROM goals WHERE id=? AND conversation_id=?", (gid, conversation_id)).fetchone()
            return self._goal(r, c) if r else None

    def active(self, conversation_id):
        with self.db.get_connection() as c:
            r = c.execute(
                "SELECT * FROM goals WHERE conversation_id=? AND state='ACTIVE' ORDER BY started_at DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
            return self._goal(r, c) if r else None

    def update_state(self, gid, state, last_error=None, conversation_id=None):
        allowed = {
            "ACTIVE", "RUNNING", "WAITING_APPROVAL", "PAUSED",
            "PAUSED_BUDGET_EXCEEDED", "RETRY_WAIT", "FAILED",
            "SUCCEEDED", "CANCELLED",
        }
        state = state.upper()
        if state not in allowed:
            raise ValueError(f"Invalid goal state {state}")
        with self.db.get_connection() as c:
            where, args = "id=?", [gid]
            if conversation_id:
                where += " AND conversation_id=?"
                args.append(conversation_id)
            cur = c.execute(
                f"UPDATE goals SET state=?,last_error=?,updated_at=? WHERE {where}",
                [state, last_error, time.time(), *args],
            )
        return self.get_scoped(gid, conversation_id) if cur.rowcount and conversation_id else (self.get(gid) if cur.rowcount else None)

    def pause(self, gid, conversation_id=None):
        return self.update_state(gid, "PAUSED", conversation_id=conversation_id)

    def resume(self, gid, conversation_id=None):
        return self.update_state(gid, "ACTIVE", conversation_id=conversation_id)

    def finish(self, gid, success, error=None):
        return self.update_state(gid, "SUCCEEDED" if (success is True or str(success).upper() == "SUCCEEDED") else "FAILED", error)

    def charge(self, gid, tokens, turn=True, continuation=False, cost=0.0, children=0):
        now = time.time()
        with self.db.get_connection() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
            if not r or r["state"] not in {"ACTIVE", "RUNNING"}:
                raise ValueError("Goal is not active")
            used = r["tokens_used"] + max(0, tokens)
            turns = r["turns_used"] + int(turn)
            cont = r["continuations_used"] + int(continuation)
            cost_used = float(r["cost_used"] or 0) + max(0.0, cost)
            children_used = int(r["children_used"] or 0) + max(0, children)
            c.execute(
                """UPDATE goals SET tokens_used=?,turns_used=?,continuations_used=?,
                   cost_used=?,children_used=?,updated_at=? WHERE id=?""",
                (used, turns, cont, cost_used, children_used, now, gid),
            )
        return self.get(gid)

    def add_gate(self, goal_id, name="QualityGate", argv=None, timeout_seconds=120):
        if not argv:
            raise ValueError("argv required")
        qid = f"gate_{uuid.uuid4().hex}"
        with self.db.get_connection() as c:
            c.execute(
                """INSERT INTO quality_gates(id,goal_id,name,argv_json,timeout_seconds,status)
                   VALUES(?,?,?,?,?,'PENDING')""",
                (qid, goal_id, name, json.dumps(argv), timeout_seconds),
            )
        return QualityGate(qid, goal_id, name, argv, "PENDING", timeout_seconds)
