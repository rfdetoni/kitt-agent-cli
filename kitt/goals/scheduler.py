from __future__ import annotations

import asyncio
import json
import logging
import time
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
            conn.execute(
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
            return True

    def check_and_execute_due(self) -> List[Dict[str, Any]]:
        """Find due active goals, enforce budgets, and trigger execution."""
        now = time.time()
        due_goals = []

        with self.db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM goals
                WHERE state = 'ACTIVE' AND heartbeat_enabled = 1 AND (next_run_at IS NULL OR next_run_at <= ?)
                ORDER BY updated_at ASC
                """,
                (now,),
            ).fetchall()

            for r in rows:
                g = self.goals._goal(r, conn)
                due_goals.append(g)

        results = []
        for goal in due_goals:
            # 1. Budget enforcement
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

            if budget_exceeded:
                with self.db.get_connection() as conn:
                    conn.execute(
                        "UPDATE goals SET state = 'PAUSED_BUDGET_EXCEEDED', last_error = ?, updated_at = ? WHERE id = ?",
                        (reason, now, goal.id),
                    )
                    conn.commit()
                results.append({"goal_id": goal.id, "status": "PAUSED_BUDGET_EXCEEDED", "reason": reason})
                continue

            # 2. Heartbeat step execution
            if self.executor:
                try:
                    step_res = self.executor(goal.conversation_id, goal.objective)
                    # Update next run if recurring
                    next_run = now + 60.0  # default 1 min heartbeat
                    with self.db.get_connection() as conn:
                        conn.execute(
                            "UPDATE goals SET next_run_at = ?, updated_at = ? WHERE id = ?",
                            (next_run, now, goal.id),
                        )
                        conn.commit()
                    results.append({"goal_id": goal.id, "status": "STEP_EXECUTED", "result": str(step_res)[:100]})
                except Exception as exc:
                    results.append({"goal_id": goal.id, "status": "STEP_FAILED", "error": str(exc)})
            else:
                results.append({"goal_id": goal.id, "status": "DUE_NO_EXECUTOR"})

        return results

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())

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
