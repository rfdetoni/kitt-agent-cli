from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from kitt.goals.service import GoalService
from kitt.history.database import HistoryDatabase

logger = logging.getLogger(__name__)


class GoalScheduler:
    def __init__(self, db: HistoryDatabase, goal_service: GoalService,
                 runtime_step_executor=None, poll_interval_seconds=5.0,
                 event_callback=None):
        self.db = db
        self.goals = goal_service
        self.executor = runtime_step_executor
        self.poll_interval = poll_interval_seconds
        self._running = False
        self._task = None
        self.worker_id = f"scheduler_{uuid.uuid4().hex[:12]}"
        self._on_event = event_callback or (lambda *_: None)

    def set_executor(self, executor):
        self.executor = executor

    def schedule_goal(self, goal_id, recurrence=None, heartbeat_enabled=True,
                      next_run_delay_seconds=0.0, resume_policy="auto",
                      retry_policy=None, owner_session_id=None):
        now = time.time()
        with self.db.get_connection() as conn:
            cur = conn.execute(
                """UPDATE goals SET scheduled_at=?,next_run_at=?,recurrence=?,
                   heartbeat_enabled=?,resume_policy=?,retry_policy=?,
                   owner_session_id=?,updated_at=? WHERE id=?""",
                (
                    now, now + max(0, next_run_delay_seconds), recurrence,
                    int(heartbeat_enabled), resume_policy,
                    json.dumps(retry_policy or {"max_retries": 3}),
                    owner_session_id, now, goal_id,
                ),
            )
        return cur.rowcount == 1

    def claim_lease(self, goal_id: str, worker_id: str = "worker", lease_duration_seconds: float = 30.0) -> bool:
        now = time.time()
        with self.db.get_connection() as conn:
            cur = conn.execute(
                """UPDATE goals SET lease_id=?,lease_owner_id=?,lease_expires_at=?,lease_heartbeat_at=?
                   WHERE id=? AND (lease_expires_at IS NULL OR lease_expires_at<=?)""",
                (f"lease_{uuid.uuid4().hex}", worker_id, now + max(1.0, lease_duration_seconds), now, goal_id, now),
            )
        return cur.rowcount == 1

    def _claim(self, goal_id, duration=30.0):
        now = time.time()
        lease_id = f"lease_{uuid.uuid4().hex}"
        with self.db.get_connection() as conn:
            cur = conn.execute(
                """UPDATE goals SET lease_id=?,lease_owner_id=?,lease_expires_at=?,
                   lease_heartbeat_at=?,state='RUNNING'
                   WHERE id=? AND state IN ('ACTIVE','RETRY_WAIT','RUNNING')
                   AND (lease_expires_at IS NULL OR lease_expires_at<=?)""",
                (lease_id, self.worker_id, now + duration, now, goal_id, now),
            )
        return lease_id if cur.rowcount == 1 else None

    def _release(self, goal_id, lease_id, *, state, next_run=None, error=None):
        with self.db.get_connection() as conn:
            conn.execute(
                """UPDATE goals SET state=?,next_run_at=?,last_error=?,updated_at=?,
                   lease_id=NULL,lease_owner_id=NULL,lease_expires_at=NULL,
                   lease_heartbeat_at=NULL
                   WHERE id=? AND lease_id=?""",
                (state, next_run, error, time.time(), goal_id, lease_id),
            )

    @staticmethod
    def _recurrence_seconds(value):
        if not value:
            return None
        raw = str(value).strip().lower()
        if raw.isdigit():
            return float(raw)
        for prefix in ("every:", "seconds:"):
            if raw.startswith(prefix):
                return max(1.0, float(raw.split(":", 1)[1]))
        raise ValueError("recurrence must be integer seconds, every:<seconds>, or seconds:<seconds>")

    def _budget_reason(self, goal, now):
        if goal.token_budget is not None and goal.tokens_used >= goal.token_budget:
            return "token budget exceeded"
        if goal.turns_used >= goal.max_turns:
            return "turn budget exceeded"
        if now - goal.started_at >= goal.max_wall_seconds:
            return "wall time budget exceeded"
        if goal.max_cost > 0 and goal.cost_used >= goal.max_cost:
            return "cost budget exceeded"
        if goal.failures_used >= goal.max_failures:
            return "failure budget exceeded"
        if goal.retries_used >= goal.max_retries:
            return "retry budget exceeded"
        if goal.children_used >= goal.max_children:
            return "child budget exceeded"
        return None

    def check_and_execute_due(self):
        now = time.time()
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """SELECT * FROM goals
                   WHERE state IN ('ACTIVE','RETRY_WAIT','RUNNING') AND heartbeat_enabled=1
                   AND (next_run_at IS NULL OR next_run_at<=?)
                   AND (lease_expires_at IS NULL OR lease_expires_at<=?)
                   ORDER BY updated_at ASC""",
                (now, now),
            ).fetchall()
            due = [self.goals._goal(r, conn) for r in rows]

        results = []
        for goal in due:
            reason = self._budget_reason(goal, now)
            if reason:
                self.goals.update_state(goal.id, "PAUSED_BUDGET_EXCEEDED", reason)
                results.append({"goal_id": goal.id, "status": "PAUSED_BUDGET_EXCEEDED", "reason": reason})
                continue

            lease = self._claim(goal.id)
            if not lease:
                continue
            if self.executor is None:
                self._release(goal.id, lease, state="ACTIVE", next_run=now + self.poll_interval, error="scheduler executor unavailable")
                results.append({"goal_id": goal.id, "status": "DUE_NO_EXECUTOR"})
                continue

            try:
                self._on_event("GoalSchedulerRun", {"goal_id": goal.id, "lease_id": lease})
                result = self.executor(goal)
                status = str(result.get("status", "FAILED")) if isinstance(result, dict) else "SUCCEEDED"
                tokens = int(result.get("tokens", 0)) if isinstance(result, dict) else 0
                cost = float(result.get("cost", 0.0)) if isinstance(result, dict) else 0.0
                self.goals.charge(goal.id, tokens, turn=True, cost=cost)
                if status == "WAITING_APPROVAL":
                    self._release(goal.id, lease, state="WAITING_APPROVAL", error=None)
                elif status == "SUCCEEDED":
                    delay = self._recurrence_seconds(goal.recurrence)
                    if delay is None:
                        self._release(goal.id, lease, state="SUCCEEDED", next_run=None)
                    else:
                        self._release(goal.id, lease, state="ACTIVE", next_run=time.time() + delay)
                else:
                    raise RuntimeError(result.get("error", status) if isinstance(result, dict) else status)
                results.append({"goal_id": goal.id, "status": status, "result": result})
            except Exception as exc:
                current = self.goals.get(goal.id)
                retries = current.retries_used + 1
                failures = current.failures_used + 1
                backoff = min(300.0, 5.0 * (2 ** min(retries, 5)))
                with self.db.get_connection() as conn:
                    conn.execute(
                        """UPDATE goals SET retries_used=?,failures_used=? WHERE id=?""",
                        (retries, failures, goal.id),
                    )
                self._release(goal.id, lease, state="RETRY_WAIT", next_run=time.time() + backoff, error=str(exc))
                self._on_event("GoalSchedulerFailure", {"goal_id": goal.id, "error": str(exc)})
                self._on_event("GoalSchedulerRetry", {"goal_id": goal.id, "retry_in": backoff})
                results.append({"goal_id": goal.id, "status": "STEP_FAILED", "error": str(exc), "retry_in": backoff})
        return results

    def start(self, interval_seconds=None):
        if interval_seconds:
            self.poll_interval = interval_seconds
        self._running = True
        try:
            self._task = asyncio.get_running_loop().create_task(self._loop())
        except RuntimeError:
            # Daemon owns the async scheduler lifecycle. Do not silently create
            # hidden threads when no event loop exists.
            self._task = None

    async def _loop(self):
        while self._running:
            try:
                self.check_and_execute_due()
            except Exception:
                logger.exception("GoalScheduler loop failure")
            await asyncio.sleep(self.poll_interval)

    def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
