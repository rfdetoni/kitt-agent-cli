"""End-to-End integration test for Dreaming Mode lifecycle."""
import unittest
from pathlib import Path
import tempfile
import time

from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.dreaming.retriever import MemoryRetriever


class TestDreamE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = RuntimeConfig(
            dream_enabled=True,
            dream_auto_enabled=False,
            persistence_enabled=True,
            history_enabled=True,
        )
        self.runtime = KittRuntime.build(str(self.root), config=self.config)
        self.workspace_id = self.runtime.workspace_id

    def tearDown(self):
        self.runtime.close()
        self.tmp.cleanup()

    def test_e2e_dream_consolidation_and_retrieval_lifecycle(self):
        now = time.time()

        # Session 1: User establishes Rule A
        c1 = "conv_1"
        self._create_session(c1, "Session 1", [
            ("USER_TURN", "Always use standard library first without adding external dependencies.")
        ], now)

        # Session 2: User repeats Rule A
        c2 = "conv_2"
        self._create_session(c2, "Session 2", [
            ("USER_TURN", "Always use standard library first.")
        ], now + 10)

        # Session 3: Architecture decision
        c3 = "conv_3"
        self._create_session(c3, "Session 3", [
            ("DECISION", "Decidimos que Context retrieval uses SQLite + FTS5.")
        ], now + 20)

        # Session 4: Known failure
        c4 = "conv_4"
        self._create_session(c4, "Session 4", [
            ("VALIDATION", "Full cold scan does not scale on large repos.")
        ], now + 30)

        # 1. Inspect (Dry Run)
        inspect_res = self.runtime.dream_service.dream(self.workspace_id, dry_run=True)
        self.assertTrue(inspect_res.run.dry_run)
        self.assertGreaterEqual(inspect_res.run.signals_found, 2)
        # Database must still have 0 committed memories
        self.assertEqual(len(self.runtime.memory_repo.get_all_memories(self.workspace_id)), 0)

        # 2. Run Dream (Commit)
        run_res = self.runtime.dream_service.dream(self.workspace_id, dry_run=False)
        self.assertFalse(run_res.run.dry_run)
        self.assertGreaterEqual(run_res.run.memories_added, 2)

        # 3. Check materialized view (.kitt/memory/MEMORY.md)
        mem_file = self.root / ".kitt" / "memory" / "MEMORY.md"
        self.assertTrue(mem_file.exists())
        mem_content = mem_file.read_text(encoding="utf-8")
        self.assertIn("Always use standard library first", mem_content)
        self.assertIn("Context retrieval uses SQLite + FTS5", mem_content)

        # 4. Check MemoryRetriever
        retriever = MemoryRetriever(self.runtime.memory_repo, max_memories=5)
        retrieved = retriever.retrieve(self.workspace_id, prompt="How do we do context retrieval in this codebase?")
        self.assertGreater(len(retrieved), 0)
        retrieved_texts = [m.content for m in retrieved]
        self.assertTrue(any("FTS5" in t for t in retrieved_texts))

        # 5. Check idempotency on immediate re-run
        rerun_res = self.runtime.dream_service.dream(self.workspace_id, dry_run=False)
        self.assertEqual(rerun_res.run.memories_added, 0)

    def _create_session(self, conv_id: str, title: str, entries: list, created_at: float):
        with self.runtime.database.get_connection() as conn:
            conn.execute(
                "INSERT INTO conversations (id, workspace_id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (conv_id, self.workspace_id, title, "COMPLETED", created_at, created_at + 5)
            )
        for etype, text in entries:
            self.runtime.session_tree.append_entry(conv_id, etype, {"content": text, "text": text, "summary": text})


if __name__ == "__main__":
    unittest.main()
