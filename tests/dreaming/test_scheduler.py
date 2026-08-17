"""Unit tests for DreamScheduler."""
import unittest
from pathlib import Path
import tempfile
import time

from kitt.core.runtime_config import RuntimeConfig
from kitt.dreaming.models import DreamRun
from kitt.dreaming.repository import MemoryRepository
from kitt.dreaming.scheduler import DreamScheduler
from kitt.dreaming.service import DreamingService
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository, resolve_workspace_identity
from kitt.history.session_tree import SessionTreeRepository


class TestDreamScheduler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = HistoryDatabase(str(self.root), in_memory=True)
        self.identity = resolve_workspace_identity(self.db, str(self.root))
        self.workspace_id = self.identity.id
        self.history_repo = HistoryRepository(self.db)
        self.session_tree = SessionTreeRepository(self.db)
        self.memory_repo = MemoryRepository(self.db)

        self.config = RuntimeConfig(
            dream_enabled=True,
            dream_auto_enabled=True,
            dream_min_interval_hours=24,
            dream_min_completed_sessions=5,
        )

        self.dream_service = DreamingService(
            db=self.db,
            memory_repo=self.memory_repo,
            history_repo=self.history_repo,
            session_tree=self.session_tree,
            root_dir=self.root,
        )

        self.is_idle = True
        self.scheduler = DreamScheduler(
            dream_service=self.dream_service,
            memory_repo=self.memory_repo,
            db=self.db,
            config=self.config,
            idle_checker=lambda: self.is_idle,
            workspace_id_getter=lambda: self.workspace_id,
        )

    def tearDown(self):
        self.scheduler.close()
        self.db.close()
        self.tmp.cleanup()

    def test_scheduler_not_eligible_when_not_idle(self):
        self._add_completed_sessions(6)
        self.is_idle = False
        self.assertFalse(self.scheduler.should_run())

    def test_scheduler_not_eligible_when_insufficient_sessions(self):
        self._add_completed_sessions(3)  # < 5 required
        self.is_idle = True
        self.assertFalse(self.scheduler.should_run())

    def test_scheduler_not_eligible_when_recent_dream_run_exists(self):
        self._add_completed_sessions(6)
        # Record a dream run from 2 hours ago (< 24h)
        run = DreamRun(
            id="run_recent",
            workspace_id=self.workspace_id,
            started_at=time.time() - 7200,
            finished_at=time.time() - 7100,
            status="COMPLETED",
            sessions_scanned=5,
            entries_scanned=20,
            signals_found=2,
            memories_added=1,
            memories_merged=0,
            memories_superseded=0,
            memories_archived=0,
            model="context-gather",
            input_tokens=0,
            output_tokens=0,
        )
        self.memory_repo.record_dream_run(run)
        self.is_idle = True
        self.assertFalse(self.scheduler.should_run())

    def test_scheduler_eligible_and_triggers_when_all_conditions_met(self):
        self._add_completed_sessions(6)
        self.is_idle = True
        self.assertTrue(self.scheduler.should_run())

        triggered = self.scheduler.trigger_if_eligible()
        self.assertTrue(triggered)

        # Wait briefly for worker thread to complete
        time.sleep(0.2)
        self.assertFalse(self.scheduler.is_dreaming)

    def _add_completed_sessions(self, count: int):
        for i in range(count):
            conv_id = f"conv_sched_{i}"
            with self.db.get_connection() as conn:
                conn.execute(
                    "INSERT INTO conversations (id, workspace_id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (conv_id, self.workspace_id, f"Session {i}", "COMPLETED", time.time() - 100, time.time() - 50)
                )


if __name__ == "__main__":
    unittest.main()
