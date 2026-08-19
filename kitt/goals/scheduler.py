from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from kitt.goals.models import Goal
from kitt.goals.service import GoalService
from kitt.history.database import HistoryDatabase

logger = logging.getLogger(__name__)


class GoalScheduler:
    """Persistent scheduler evaluating autonomous goals, recurrence, and heartbeats under strict budgets."""

    def __init__(
        self,
        db: HistoryDatabase,
        goal_service: GoalService,
        runtime_step_executor: Optional[Callable[[str, str], Any]] = None,
        poll_interval_seconds: float = 5.0,
    ):
        self.db = db
        self.goals = goal_service
        self.executor = runtime_step_executor
        self.poll_interval = poll_interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def schedule_goal(
        self,
        goal_id: str,
        recurrence: Optional[str] = None,
        heartbeat_enabled: bool = True,
        next_run_delay_seconds: float = 0.0,
        resume_policy: str = "auto",
        retry_policy: Optional[Dict[str, Any]] = None,
        owner_session_id: Optional[str] = None,
    ) -> bool:
        """Configure scheduling parameters for a persistent goal."""
        now = time.time()
        next_run_at = now + max(0.0, next_run_delay_seconds)
        retry_json = json.dumps(retry_policy or {"max_retries": 3, "retry_count": 0})

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE goals SET
                    scheduled_at = ?,
                    next_run_at = ?,
                    recurrence = ?,
                    heartbeat_enabled = ?,
                    resume_policy = ?,
                    retry_policy = ?,
                    owner_session_id = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    now,
                    next_run_at,
                    recurrence,
                    1 if heartbeat_enabled else 0,
                    resume_policy,
                    retry_json,
                    owner_session_id,
                    now,
                    goal_id,
                ),
            )
            conn.commit()
            return cur.rowcount > 0

    def check_and_execute_due(self) -> List[Dict[str, Any]]:
        """Find due active goals, acquire atomic lease, enforce budgets, and trigger step execution."""
        now = time.time()
        due_goals = []

        with self.db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM goals
                WHERE state = 'ACTIVE' AND heartbeat_enabled = 1
                  AND (next_run_at IS NULL OR next_run_at <= ?)
                  AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                ORDER BY updated_at ASC
                """,
                (now, now),
            ).fetchall()

            for r in rows:
                g = self.goals._goal(r, conn)
                due_goals.append(g)

        results = []
        for goal in due_goals:
            lease_id = f"lease_{uuid.uuid4().hex}"
            lease_expires_at = now + 30.0

            # Atomic lease claim
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE goals SET lease_id = ?, lease_expires_at = ?
                    WHERE id = ? AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                    """,
                    (lease_id, lease_expires_at, goal.id, now),
                )
                conn.commit()
                if cur.rowcount == 0:
                    continue  # Claimed by concurrent worker

            # 1. Comprehensive Budget Enforcement
            elapsed = now - goal.started_at
            budget_exceeded = False
            reason = ""

            if goal.token_budget is not None and goal.tokens_used >= goal.token_budget:
                budget_exceeded = True
                reason = f"Token budget exceeded ({goal.tokens_used} >= {goal.token_budget})"
            elif goal.turns_used >= goal.max_turns:
                budget_exceeded = True
                reason = f"Turn limit exceeded ({goal.turns_used} >= {goal.max_turns})"
            elif elapsed >= goal.max_wall_seconds:
                budget_exceeded = True
                reason = f"Wall time exceeded ({int(elapsed)}s >= {goal.max_wall_seconds}s)"
            elif goal.failures_used >= goal.max_failures:
                budget_exceeded = True
                reason = f"Failure limit reached ({goal.failures_used} >= {goal.max_failures})"
            elif goal.retries_used >= goal.max_retries:
                budget_exceeded = True
                reason = f"Retry limit reached ({goal.retries_used} >= {goal.max_retries})"

            if budget_exceeded:
                with self.db.get_connection() as conn:
                    conn.execute(
                        "UPDATE goals SET state = 'PAUSED_BUDGET_EXCEEDED', last_error = ?, updated_at = ?, lease_expires_at = NULL WHERE id = ?",
                        (reason, now, goal.id),
                    )
                    conn.commit()
                results.append({"goal_id": goal.id, "status": "PAUSED_BUDGET_EXCEEDED", "reason": reason})
                continue

            # 2. Heartbeat step execution
            if self.executor:
                try:
                    step_res = self.executor(goal.conversation_id, goal.objective)
                    # Update next run if recurring, or default heartbeat
                    recurrence_delay = 60.0
                    if goal.recurrence and goal.recurrence.isdigit():
                        recurrence_delay = float(goal.recurrence)
                    next_run = now + recurrence_delay

                    with self.db.get_connection() as conn:
                        conn.execute(
                            "UPDATE goals SET next_run_at = ?, updated_at = ?, lease_expires_at = NULL WHERE id = ?",
                            (next_run, now, goal.id),
                        )
                        conn.commit()
                    results.append({"goal_id": goal.id, "status": "STEP_EXECUTED", "result": str(step_res)[:100]})
                except Exception as exc:
                    retries = goal.retries_used + 1
                    failures = goal.failures_used + 1
                    backoff_delay = min(300.0, 5.0 * (2 ** min(retries, 5)))
                    next_run = now + backoff_delay

                    with self.db.get_connection() as conn:
                        conn.execute(
                            "UPDATE goals SET retries_used = ?, failures_used = ?, next_run_at = ?, last_error = ?, updated_at = ?, lease_expires_at = NULL WHERE id = ?",
                            (retries, failures, next_run, str(exc), now, goal.id),
                        )
                        conn.commit()
                    results.append({"goal_id": goal.id, "status": "STEP_FAILED", "error": str(exc), "retry_in": backoff_delay})
            else:
                with self.db.get_connection() as conn:
                    conn.execute("UPDATE goals SET lease_expires_at = NULL WHERE id = ?", (goal.id,))
                    conn.commit()
                results.append({"goal_id": goal.id, "status": "DUE_NO_EXECUTOR"})

        return results

    def start(self, interval_seconds: Optional[float] = None) -> None:
        if interval_seconds:
            self.poll_interval = interval_seconds
        self._running = True
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._loop())
        except RuntimeError:
            pass

    async def _loop(self) -> None:
        while self._running:
            try:
                self.check_and_execute_due()
            except Exception as exc:
                logger.error(f"Error in GoalScheduler loop: {exc}")
            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
