import json,time,uuid
from typing import List,Optional
from kitt.goals.models import Goal,QualityGate
from kitt.history.database import HistoryDatabase

class GoalService:
    TERMINAL={"SUCCEEDED","FAILED","CANCELLED","BUDGET_EXHAUSTED"}
    def __init__(self,db:HistoryDatabase): self.db=db

    def _get_gates(self, conn, goal_id: str) -> List[QualityGate]:
        rows = conn.execute("SELECT * FROM quality_gates WHERE goal_id=?", (goal_id,)).fetchall()
        gates = []
        for r in rows:
            d = dict(r)
            argv = json.loads(d.get("argv_json") or "[]")
            gates.append(QualityGate(
                id=d["id"],
                goal_id=d["goal_id"],
                name=d.get("name") or "QualityGate",
                argv=argv,
                status=d.get("status", "PENDING"),
                timeout_seconds=d.get("timeout_seconds", 120),
                last_exit_code=d.get("last_exit_code"),
                last_output_artifact_id=d.get("last_output_artifact_id")
            ))
        return gates

    def _goal(self, r, conn=None) -> Goal:
        gid = r["id"]
        gates = []
        if conn:
            gates = self._get_gates(conn, gid)
        else:
            with self.db.get_connection() as c:
                gates = self._get_gates(c, gid)
        return Goal(
            r["id"], r["conversation_id"], r["objective"], r["state"], r["token_budget"],
            r["max_turns"], r["max_wall_seconds"], r["tokens_used"], r["turns_used"],
            r["continuations_used"], json.loads(r["success_criteria_json"]), r["started_at"],
            r["updated_at"], r["completed_at"], r["last_error"], gates
        )

    def create(self, conversation_id: str, objective: str, success_criteria: Optional[List[str]] = None,
               token_budget: Optional[int] = None, max_turns: int = 12, max_wall_seconds: int = 1800) -> Goal:
        if not objective.strip(): raise ValueError("Goal objective required")
        gid = f"goal_{uuid.uuid4().hex}"; now = time.time()
        with self.db.get_connection() as c:
            c.execute("""INSERT INTO goals(id,conversation_id,objective,state,token_budget,max_turns,
                max_wall_seconds,success_criteria_json,started_at,updated_at)
                VALUES(?,?,?,'ACTIVE',?,?,?,?,?,?)""", (gid, conversation_id, objective.strip(),
                token_budget, max(1, max_turns), max(1, max_wall_seconds),
                json.dumps(success_criteria or []), now, now))
        return self.get(gid)

    def get(self, gid: str) -> Optional[Goal]:
        with self.db.get_connection() as c:
            r = c.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
            return self._goal(r, c) if r else None

    def active(self, conversation_id: str) -> Optional[Goal]:
        with self.db.get_connection() as c:
            r = c.execute("SELECT * FROM goals WHERE conversation_id=? AND state='ACTIVE' ORDER BY started_at DESC LIMIT 1", (conversation_id,)).fetchone()
            return self._goal(r, c) if r else None

    def charge(self, gid: str, tokens: int, turn: bool = True, continuation: bool = False) -> Goal:
        with self.db.get_connection() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
            if not r or r["state"] != "ACTIVE": raise ValueError("Goal is not active")
            used = r["tokens_used"] + max(tokens, 0); turns = r["turns_used"] + int(turn); cont = r["continuations_used"] + int(continuation)
            state = "ACTIVE"; elapsed = time.time() - r["started_at"]
            if r["token_budget"] is not None and used > r["token_budget"]: state = "BUDGET_EXHAUSTED"
            elif turns > r["max_turns"] or elapsed > r["max_wall_seconds"]: state = "BUDGET_EXHAUSTED"
            c.execute("UPDATE goals SET tokens_used=?,turns_used=?,continuations_used=?,state=?,updated_at=?,completed_at=? WHERE id=?",
                (used, turns, cont, state, time.time(), time.time() if state != "ACTIVE" else None, gid))
        return self.get(gid)

    def finish(self, gid: str, success: bool | str, error: Optional[str] = None) -> Goal:
        if isinstance(success, str):
            is_success = (success.strip().upper() == "SUCCEEDED" or success.strip().lower() == "true")
        else:
            is_success = bool(success)
        state = "SUCCEEDED" if is_success else "FAILED"; now = time.time()
        with self.db.get_connection() as c:
            c.execute("UPDATE goals SET state=?,last_error=?,updated_at=?,completed_at=? WHERE id=? AND state='ACTIVE'", (state, error, now, now, gid))
        return self.get(gid)

    def add_gate(self, goal_id: str, name: str = "QualityGate", argv: Optional[List[str]] = None, timeout_seconds: int = 120) -> QualityGate:
        if argv is None and isinstance(name, list):
            argv = name
            name = "QualityGate"
        if not argv or any(not isinstance(x, str) or not x for x in argv): raise ValueError("argv required")
        qid = f"gate_{uuid.uuid4().hex}"
        with self.db.get_connection() as c:
            try:
                c.execute("INSERT INTO quality_gates(id,goal_id,name,argv_json,timeout_seconds,status) VALUES(?,?,?,?,?,'PENDING')", (qid, goal_id, name, json.dumps(argv), timeout_seconds))
            except Exception:
                c.execute("INSERT INTO quality_gates(id,goal_id,argv_json,status) VALUES(?,?,?,'PENDING')", (qid, goal_id, json.dumps(argv)))
        return QualityGate(qid, goal_id, name, argv, "PENDING", timeout_seconds)
