"""Unit tests for MemoryRetriever."""
import unittest
from pathlib import Path
import tempfile
import time

from kitt.dreaming.models import MemoryRecord
from kitt.dreaming.repository import MemoryRepository
from kitt.dreaming.retriever import MemoryRetriever
from kitt.history.database import HistoryDatabase


from kitt.history.repository import resolve_workspace_identity


class TestMemoryRetriever(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = HistoryDatabase(str(self.root), in_memory=True)
        self.identity = resolve_workspace_identity(self.db, str(self.root))
        self.workspace_id = self.identity.id
        self.repo = MemoryRepository(self.db)
        self.retriever = MemoryRetriever(self.repo, max_memories=4)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_retriever_selects_relevant_and_pinned_memories(self):
        # 1. Pinned rule
        self.repo.add_direct_memory(self.workspace_id, "Always use standard library first", kind="PROJECT_RULE", pinned=True)
        # 2. Architecture decision matching Maven
        self.repo.add_direct_memory(self.workspace_id, "Prefer ./mvnw when Maven wrapper is present", kind="WORKING_PATTERN", pinned=False)
        # 3. Irrelevant fact
        self.repo.add_direct_memory(self.workspace_id, "Legacy backend port was 9090", kind="TECHNICAL_FACT", pinned=False)

        results = self.retriever.retrieve(self.workspace_id, prompt="Run the test suite using maven wrapper")
        self.assertLessEqual(len(results), 4)

        contents = [m.content for m in results]
        self.assertIn("Prefer ./mvnw when Maven wrapper is present", contents)
        self.assertIn("Always use standard library first", contents)

    def test_retriever_excludes_superseded_and_archived_memories(self):
        m1 = self.repo.add_direct_memory(self.workspace_id, "Active fact about router", kind="TECHNICAL_FACT")
        m2 = self.repo.add_direct_memory(self.workspace_id, "Old router was v1", kind="TECHNICAL_FACT")
        self.repo.set_memory_status(m2.id, "SUPERSEDED")

        results = self.retriever.retrieve(self.workspace_id, prompt="Tell me about the router")
        ids = [m.id for m in results]
        self.assertIn(m1.id, ids)
        self.assertNotIn(m2.id, ids)

    def test_retriever_touches_access_count(self):
        m = self.repo.add_direct_memory(self.workspace_id, "Access count test fact", kind="TECHNICAL_FACT")
        self.assertEqual(m.access_count, 0)

        self.retriever.retrieve(self.workspace_id, prompt="Access count test fact", touch_access=True)
        updated = self.repo.get_memory(m.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.access_count, 1)
        self.assertIsNotNone(updated.last_accessed_at)


if __name__ == "__main__":
    unittest.main()
