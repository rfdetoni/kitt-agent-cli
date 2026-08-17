"""Unit tests for Phase 4: PRUNE & INDEX."""
import unittest
import time
from pathlib import Path
import tempfile

from kitt.dreaming.models import DreamSnapshot, MemoryRecord
from kitt.dreaming.prune_index import DreamPruneAndIndexPhase
from kitt.dreaming.repository import MemoryRepository
from kitt.history.database import HistoryDatabase
from kitt.history.repository import resolve_workspace_identity


class TestDreamPruneAndIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = HistoryDatabase(str(self.root), in_memory=True)
        self.identity = resolve_workspace_identity(self.db, str(self.root))
        self.workspace_id = self.identity.id
        self.repo = MemoryRepository(self.db)
        self.prune_phase = DreamPruneAndIndexPhase(self.repo, archive_threshold_days=30.0)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_salience_calculation(self):
        now = time.time()
        fresh_mem = MemoryRecord(
            id="mem_fresh",
            workspace_id="ws_1",
            kind="TECHNICAL_FACT",
            content="Port is 8080",
            normalized_content="Port is 8080",
            status="ACTIVE",
            importance=0.8,
            confidence=1.0,
            created_at=now,
            updated_at=now,
            access_count=5,
        )
        salience_fresh = self.prune_phase.calculate_salience(fresh_mem, now)
        self.assertGreater(salience_fresh, 0.8)

        old_mem = MemoryRecord(
            id="mem_old",
            workspace_id="ws_1",
            kind="TECHNICAL_FACT",
            content="Temporary debug flag was true",
            normalized_content="Temporary debug flag was true",
            status="ACTIVE",
            importance=0.2,
            confidence=0.7,
            created_at=now - (90 * 86400),
            updated_at=now - (90 * 86400),
            access_count=0,
        )
        salience_old = self.prune_phase.calculate_salience(old_mem, now)
        self.assertLess(salience_old, 0.1)

    def test_protected_kinds_never_archived_by_age(self):
        now = time.time()
        old_rule = MemoryRecord(
            id="mem_rule",
            workspace_id="ws_1",
            kind="PROJECT_RULE",
            content="Prefer standard library first",
            normalized_content="Prefer standard library first",
            status="ACTIVE",
            importance=0.9,
            confidence=1.0,
            created_at=now - (120 * 86400),
            updated_at=now - (120 * 86400),
            pinned=False,
        )
        snapshot = DreamSnapshot(
            workspace_id="ws_1",
            memories=(old_rule,),
            recent_sessions=(),
            recent_entries=(),
            last_dream_at=None,
            completed_sessions_since_last_dream=1,
            generated_at=now,
        )

        archived, proj = self.prune_phase.prune_and_index(self.workspace_id, snapshot, root_dir=self.root)
        self.assertEqual(len(archived), 0)

    def test_rebuild_materialized_view(self):
        self.repo.add_direct_memory(self.workspace_id, "Always use standard library first", kind="PROJECT_RULE", pinned=True)
        self.repo.add_direct_memory(self.workspace_id, "Context retrieval uses SQLite + FTS5", kind="ARCHITECTURE_DECISION")

        view = self.repo.rebuild_materialized_view(self.workspace_id, root_dir=self.root)
        self.assertIn("# K.I.T.T. Memory", view)
        self.assertIn("## Project Rules", view)
        self.assertIn("Always use standard library first", view)
        self.assertIn("Context retrieval uses SQLite + FTS5", view)

        # Check file on disk
        mem_file = self.root / ".kitt" / "memory" / "MEMORY.md"
        self.assertTrue(mem_file.exists())
        self.assertEqual(mem_file.read_text(encoding="utf-8"), view)


if __name__ == "__main__":
    unittest.main()
