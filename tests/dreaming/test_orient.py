"""Unit tests for Phase 1: ORIENT."""
import unittest
from pathlib import Path
import tempfile
import time

from kitt.dreaming.models import DreamSnapshot, MemoryRecord
from kitt.dreaming.orient import DreamOrientPhase
from kitt.dreaming.repository import MemoryRepository
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository, resolve_workspace_identity
from kitt.history.session_tree import SessionTreeRepository


class TestDreamOrient(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = HistoryDatabase(str(self.root), in_memory=True)
        self.identity = resolve_workspace_identity(self.db, str(self.root))
        self.workspace_id = self.identity.id
        self.history_repo = HistoryRepository(self.db)
        self.session_tree = SessionTreeRepository(self.db)
        self.memory_repo = MemoryRepository(self.db)
        self.orient_phase = DreamOrientPhase(
            self.db, self.memory_repo, self.history_repo, self.session_tree,
            max_sessions=5, max_entries=20
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_orient_with_empty_memory_and_history(self):
        snapshot = self.orient_phase.orient(self.workspace_id)
        self.assertIsInstance(snapshot, DreamSnapshot)
        self.assertEqual(snapshot.workspace_id, self.workspace_id)
        self.assertEqual(len(snapshot.memories), 0)
        self.assertEqual(len(snapshot.recent_sessions), 0)
        self.assertEqual(len(snapshot.recent_entries), 0)
        self.assertIsNone(snapshot.last_dream_at)
        self.assertEqual(snapshot.completed_sessions_since_last_dream, 0)

    def test_orient_collects_memories_and_bounded_sessions(self):
        # 1. Add some direct memories
        self.memory_repo.add_direct_memory(self.workspace_id, "Always use stdlib first", kind="PROJECT_RULE")
        self.memory_repo.add_direct_memory(self.workspace_id, "Context uses FTS5", kind="ARCHITECTURE_DECISION")

        # 2. Add multiple conversations and session entries
        for i in range(8):
            conv_id = f"conv_{i}"
            with self.db.get_connection() as conn:
                conn.execute(
                    "INSERT INTO conversations (id, workspace_id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (conv_id, self.workspace_id, f"Conv {i}", "COMPLETED" if i % 2 == 0 else "ACTIVE", time.time(), time.time() + i)
                )
            self.session_tree.append_entry(conv_id, "USER_TURN", {"content": f"Do step {i}"})
            self.session_tree.append_entry(conv_id, "DECISION", {"text": f"Decided method {i}"})

        snapshot = self.orient_phase.orient(self.workspace_id)

        # Memories should match
        self.assertEqual(len(snapshot.memories), 2)
        self.assertTrue(all(m.workspace_id == self.workspace_id for m in snapshot.memories))

        # Sessions must be bounded by max_sessions = 5
        self.assertLessEqual(len(snapshot.recent_sessions), 5)
        # Entries must be bounded by max_entries = 20
        self.assertLessEqual(len(snapshot.recent_entries), 20)

    def test_orient_ignores_other_workspace(self):
        other_ws = "other_ws_123"
        with self.db.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO workspaces (id, canonical_path_hash, display_name, created_at, last_opened_at) VALUES (?, ?, ?, ?, ?)",
                         (other_ws, "hash_other", "Other", time.time(), time.time()))
            conn.execute("INSERT INTO conversations (id, workspace_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                         ("other_conv", other_ws, "Other Conv", time.time(), time.time()))

        self.memory_repo.add_direct_memory(other_ws, "Foreign memory", kind="PROJECT_RULE")

        snapshot = self.orient_phase.orient(self.workspace_id)
        self.assertEqual(len(snapshot.memories), 0)
        self.assertEqual(len(snapshot.recent_sessions), 0)


if __name__ == "__main__":
    unittest.main()
