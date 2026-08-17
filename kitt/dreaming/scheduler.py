"""DreamScheduler: Manages automated idle-triggered background Dreaming Mode execution."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional, Dict, Any

from kitt.core.runtime_config import RuntimeConfig
from kitt.dreaming.repository import MemoryRepository
from kitt.dreaming.service import DreamingService
from kitt.history.database import HistoryDatabase

logger = logging.getLogger(__name__)


class DreamScheduler:
    """Evaluates idle eligibility and coordinates single-worker background dream runs."""

    def __init__(
        self,
        dream_service: DreamingService,
        memory_repo: MemoryRepository,
        db: HistoryDatabase,
        config: RuntimeConfig,
        idle_checker: Optional[Callable[[], bool]] = None,
        workspace_id_getter: Optional[Callable[[], str]] = None,
    ):
        self.dream_service = dream_service
        self.memory_repo = memory_repo
        self.db = db
        self.config = config
        self.idle_checker = idle_checker or (lambda: True)
        self.workspace_id_getter = workspace_id_getter or (lambda: "default")

        self._lock = threading.Lock()
        self._is_dreaming = False
        self._current_thread: Optional[threading.Thread] = None
        self._closed = False

    @property
    def is_dreaming(self) -> bool:
        return self._is_dreaming

    def should_run(self, workspace_id: Optional[str] = None) -> bool:
        """Determines if the runtime is eligible for an automated background dream run."""
        if self._closed or self._is_dreaming:
            return False

        # Config checks
        dream_enabled = getattr(self.config, "dream_enabled", True)
        dream_auto = getattr(self.config, "dream_auto_enabled", False)
        if not (dream_enabled and dream_auto and self.config.persistence_enabled):
            return False

        # Runtime idle check
        if not self.idle_checker():
            return False

        ws_id = workspace_id or self.workspace_id_getter()
        now = time.time()

        # 1. Interval check (>= dream_min_interval_hours)
        min_interval_seconds = getattr(self.config, "dream_min_interval_hours", 24) * 3600.0
        last_run = self.memory_repo.get_last_dream_run(ws_id)
        if last_run and last_run.finished_at:
            if (now - last_run.finished_at) < min_interval_seconds:
                return False

        # 2. Completed sessions check (>= dream_min_completed_sessions)
        min_sessions = getattr(self.config, "dream_min_completed_sessions", 5)
        last_dream_at = last_run.finished_at if last_run else 0.0

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) FROM conversations
                WHERE workspace_id = ? AND status != 'ACTIVE' AND updated_at >= ?
                """,
                (ws_id, last_dream_at)
            )
            row = cursor.fetchone()
            completed_count = row[0] if row else 0

        if completed_count < min_sessions:
            return False

        return True

    def trigger_if_eligible(self, workspace_id: Optional[str] = None) -> bool:
        """Triggers a background dream if eligible and lock acquired."""
        ws_id = workspace_id or self.workspace_id_getter()
        if not self.should_run(ws_id):
            return False

        with self._lock:
            if self._is_dreaming or self._closed:
                return False
            self._is_dreaming = True

            def worker():
                try:
                    dry_run = not getattr(self.config, "dream_auto_commit", False)
                    self.dream_service.dream(ws_id, dry_run=dry_run)
                except Exception as exc:
                    logger.debug("Background dream execution finished with: %s", exc)
                finally:
                    self._is_dreaming = False

            t = threading.Thread(target=worker, name=f"KittDreamWorker-{ws_id[:8]}", daemon=True)
            self._current_thread = t
            t.start()
            return True

    def cancel(self) -> None:
        """Signals cancellation to the running dream service."""
        self.dream_service.cancel()

    def close(self, timeout: float = 2.0) -> None:
        """Shuts down scheduler and waits for any active worker to terminate."""
        self._closed = True
        self.cancel()
        with self._lock:
            if self._current_thread and self._current_thread.is_alive():
                self._current_thread.join(timeout=timeout)
