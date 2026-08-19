from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from typing import Optional

from kitt.goals.service import GoalService
from kitt.history.database import HistoryDatabase


logger = logging.getLogger(__name__)


class GoalScheduler:
    """Durable goal scheduler with lease ownership and active heartbeat renewal."""

    def __init__(
        self,
        db: HistoryDatabase,
        goal_service: GoalService,
        runtime_step_executor=None,
        poll_interval_seconds=5.0,
        event_callback=None,
        lease_duration_seconds: float = 30.0,
    ):
        self.db = db
        self.goals = goal_service
        self.executor = runtime_step_executor
        self.poll_interval = max(0.1, float(poll_interval_seconds))
        self.lease_duration = max(1.0, float(lease_duration_seconds))
        self._running = False
        self._task = None
        self._active_run_done = threading.Event()
        self._active_run_done.set()
        self.worker_id = f"scheduler_{uuid.uuid4().hex[:12]}"
        self._on_event = event_callback or (lambda *_: None)

    def set_executor(self, executor):
        self.executor = executor

    def schedule_goal(
        self,
        goal_id,
        recurrence=None,
        heartbeat_enabled=True,
        next_run_delay_seconds=0.0,
        resume_policy="auto",
        retry_policy=None,
        owner_session_id=None,
    ):
        now = time.time()
        with self.db.get_connection() as connection:
            cursor = connection.execute(
                """UPDATE goals SET scheduled_at=?,next_run_at=?,recurrence=?,
                   heartbeat_enabled=?,resume_policy=?,retry_policy=?,
                   owner_session_id=?,updated_at=? WHERE id=?""",
                (
                    now,
                    now + max(0.0, float(next_run_delay_seconds)),
                    recurrence,
                    int(bool(heartbeat_enabled)),
                    resume_policy,
                    json.dumps(retry_policy or {"max_retries": 3}),
                    owner_session_id,
                    now,
                    goal_id,
                ),
            )
        return cursor.rowcount == 1

    def claim_lease(
        self,
        goal_id: str,
        worker_id: str = "worker",
        lease_duration_seconds: float = 30.0,
    ) -> bool:
        now = time.time()
        duration = max(1.0, float(lease_duration_seconds))
        with self.db.get_connection() as connection:
            cursor = connection.execute(
                """UPDATE goals SET lease_id=?,lease_owner_id=?,lease_expires_at=?,lease_heartbeat_at=?
                   WHERE id=? AND (lease_expires_at IS NULL OR lease_expires_at<=?)""",
                (
                    f"lease_{uuid.uuid4().hex}",
                    worker_id,
                    now + duration,
                    now,
                    goal_id,
                    now,
                ),
            )
        return cursor.rowcount == 1

    def _claim(self, goal_id: str) -> Optional[str]:
        now = time.time()
        lease_id = f"lease_{uuid.uuid4().hex}"
        with self.db.get_connection() as connection:
            cursor = connection.execute(
                """UPDATE goals SET lease_id=?,lease_owner_id=?,lease_expires_at=?,
                   lease_heartbeat_at=?,state='RUNNING'
                   WHERE id=? AND state IN ('ACTIVE','RETRY_WAIT','RUNNING')
                   AND (lease_expires_at IS NULL OR lease_expires_at<=?)""",
                (
                    lease_id,
                    self.worker_id,
                    now + self.lease_duration,
                    now,
                    goal_id,
                    now,
                ),
            )
        return lease_id if cursor.rowcount == 1 else None

    def _heartbeat_lease_once(self, goal_id: str, lease_id: str) -> bool:
        now = time.time()
        with self.db.get_connection() as connection:
            cursor = connection.execute(
                """UPDATE goals SET lease_expires_at=?,lease_heartbeat_at=?
                   WHERE id=? AND lease_id=? AND lease_owner_id=? AND state='RUNNING'""",
                (
                    now + self.lease_duration,
                    now,
                    goal_id,
                    lease_id,
                    self.worker_id,
                ),
            )
        return cursor.rowcount == 1

    def _heartbeat_loop(
        self,
        goal_id: str,
        lease_id: str,
        stop_event: threading.Event,
    ) -> None:
        interval = max(0.25, min(10.0, self.lease_duration / 3.0))
        while not stop_event.wait(interval):
            try:
                if not self._heartbeat_lease_once(goal_id, lease_id):
                    return
            except Exception:
                logger.exception("Goal lease heartbeat failed for %s", goal_id)

    def _execute_with_heartbeat(self, goal, lease_id: str):
        stop_event = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(goal.id, lease_id, stop_event),
            name=f"kitt-goal-heartbeat-{goal.id[:8]}",
            daemon=True,
        )
        heartbeat.start()
        try:
            return self.executor(goal)
        finally:
            stop_event.set()
            heartbeat.join(timeout=1.0)

    def _release(
        self,
        goal_id,
        lease_id,
        *,
        state,
        next_run=None,
        error=None,
    ) -> bool:
        with self.db.get_connection() as connection:
            cursor = connection.execute(
                """UPDATE goals SET state=?,next_run_at=?,last_error=?,updated_at=?,
                   lease_id=NULL,lease_owner_id=NULL,lease_expires_at=NULL,
                   lease_heartbeat_at=NULL
                   WHERE id=? AND lease_id=? AND lease_owner_id=?""",
                (
                    state,
                    next_run,
                    error,
                    time.time(),
                    goal_id,
                    lease_id,
                    self.worker_id,
                ),
            )
        return cursor.rowcount == 1

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
        raise ValueError(
            "recurrence must be integer seconds, every:<seconds>, or seconds:<seconds>"
        )

    @staticmethod
    def _budget_reason(goal, now):
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

    def _record_retry_failure(self, goal_id: str, error: str) -> dict:
        current = self.goals.get(goal_id)
        if current is None:
            return {"status": "FAILED", "error": error}

        retries = current.retries_used + 1
        failures = current.failures_used + 1
        with self.db.get_connection() as connection:
            connection.execute(
                "UPDATE goals SET retries_used=?,failures_used=? WHERE id=?",
                (retries, failures, goal_id),
            )

        exhausted = retries >= current.max_retries or failures >= current.max_failures
        if exhausted:
            return {
                "status": "PAUSED_BUDGET_EXCEEDED",
                "error": error,
                "retries": retries,
                "failures": failures,
            }

        backoff = min(300.0, 5.0 * (2 ** min(retries, 5)))
        return {
            "status": "RETRY_WAIT",
            "error": error,
            "retry_in": backoff,
            "retries": retries,
            "failures": failures,
        }

    def check_and_execute_due(self):
        now = time.time()
        with self.db.get_connection() as connection:
            rows = connection.execute(
                """SELECT * FROM goals
                   WHERE state IN ('ACTIVE','RETRY_WAIT','RUNNING') AND heartbeat_enabled=1
                   AND (next_run_at IS NULL OR next_run_at<=?)
                   AND (lease_expires_at IS NULL OR lease_expires_at<=?)
                   ORDER BY updated_at ASC""",
                (now, now),
            ).fetchall()
            due = [self.goals._goal(row, connection) for row in rows]

        results = []
        for goal in due:
            reason = self._budget_reason(goal, now)
            if reason:
                self.goals.update_state(
                    goal.id,
                    "PAUSED_BUDGET_EXCEEDED",
                    reason,
                    conversation_id=goal.conversation_id,
                )
                results.append(
                    {
                        "goal_id": goal.id,
                        "status": "PAUSED_BUDGET_EXCEEDED",
                        "reason": reason,
                    }
                )
                continue

            lease_id = self._claim(goal.id)
            if not lease_id:
                continue

            if self.executor is None:
                self._release(
                    goal.id,
                    lease_id,
                    state="ACTIVE",
                    next_run=now + self.poll_interval,
                    error="scheduler executor unavailable",
                )
                results.append(
                    {"goal_id": goal.id, "status": "DUE_NO_EXECUTOR"}
                )
                continue

            try:
                self._on_event(
                    "GoalSchedulerRun",
                    {"goal_id": goal.id, "lease_id": lease_id},
                )
                result = self._execute_with_heartbeat(goal, lease_id)
                status = (
                    str(result.get("status", "FAILED"))
                    if isinstance(result, dict)
                    else "SUCCEEDED"
                )
                tokens = int(result.get("tokens", 0)) if isinstance(result, dict) else 0
                cost = float(result.get("cost", 0.0)) if isinstance(result, dict) else 0.0
                self.goals.charge(goal.id, tokens, turn=True, cost=cost)

                if status == "WAITING_APPROVAL":
                    self._release(
                        goal.id,
                        lease_id,
                        state="WAITING_APPROVAL",
                        error=None,
                    )
                elif status == "SUCCEEDED":
                    delay = self._recurrence_seconds(goal.recurrence)
                    if delay is None:
                        self._release(
                            goal.id,
                            lease_id,
                            state="SUCCEEDED",
                            next_run=None,
                        )
                    else:
                        self._release(
                            goal.id,
                            lease_id,
                            state="ACTIVE",
                            next_run=time.time() + delay,
                        )
                else:
                    message = (
                        result.get("error", status)
                        if isinstance(result, dict)
                        else status
                    )
                    raise RuntimeError(message)

                results.append(
                    {"goal_id": goal.id, "status": status, "result": result}
                )
            except Exception as exc:
                retry = self._record_retry_failure(goal.id, str(exc))
                if retry["status"] == "PAUSED_BUDGET_EXCEEDED":
                    self._release(
                        goal.id,
                        lease_id,
                        state="PAUSED_BUDGET_EXCEEDED",
                        next_run=None,
                        error=str(exc),
                    )
                    self._on_event(
                        "GoalSchedulerFailure",
                        {"goal_id": goal.id, "error": str(exc), "terminal": True},
                    )
                    results.append(
                        {
                            "goal_id": goal.id,
                            "status": "PAUSED_BUDGET_EXCEEDED",
                            "error": str(exc),
                        }
                    )
                else:
                    backoff = retry["retry_in"]
                    self._release(
                        goal.id,
                        lease_id,
                        state="RETRY_WAIT",
                        next_run=time.time() + backoff,
                        error=str(exc),
                    )
                    self._on_event(
                        "GoalSchedulerFailure",
                        {"goal_id": goal.id, "error": str(exc)},
                    )
                    self._on_event(
                        "GoalSchedulerRetry",
                        {"goal_id": goal.id, "retry_in": backoff},
                    )
                    results.append(
                        {
                            "goal_id": goal.id,
                            "status": "STEP_FAILED",
                            "error": str(exc),
                            "retry_in": backoff,
                        }
                    )
        return results

    def start(self, interval_seconds=None):
        if interval_seconds is not None:
            self.poll_interval = max(0.1, float(interval_seconds))
        if self._running and self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Daemon owns the async scheduler lifecycle. No hidden worker thread.
            self._running = False
            self._task = None
            return
        self._running = True
        self._task = loop.create_task(self._loop())

    def _run_due_blocking(self):
        self._active_run_done.clear()
        try:
            return self.check_and_execute_due()
        finally:
            self._active_run_done.set()

    async def _loop(self):
        try:
            while self._running:
                try:
                    # LLM-backed goal steps are blocking. Keep them off the daemon
                    # event loop so IPC/attach/cancel remain responsive.
                    await asyncio.to_thread(self._run_due_blocking)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("GoalScheduler loop failure")
                await asyncio.sleep(self.poll_interval)
        finally:
            self._running = False

    def stop(self, wait_timeout: float = 30.0):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        if not self._active_run_done.wait(timeout=max(0.0, float(wait_timeout))):
            logger.warning(
                "GoalScheduler shutdown timed out while an execution is still active"
            )
