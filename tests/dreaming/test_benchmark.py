"""Benchmark suite for Dreaming Mode performance and token compression."""
import unittest
from pathlib import Path
import tempfile
import time

from kitt.context_filter.prompt_budget import TokenCounter
from kitt.dreaming.models import MemoryRecord
from kitt.dreaming.repository import MemoryRepository
from kitt.dreaming.service import DreamingService
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository, resolve_workspace_identity
from kitt.history.session_tree import SessionTreeRepository


class TestDreamBenchmark(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = HistoryDatabase(str(self.root), in_memory=True)
        self.identity = resolve_workspace_identity(self.db, str(self.root))
        self.workspace_id = self.identity.id
        self.history_repo = HistoryRepository(self.db)
        self.session_tree = SessionTreeRepository(self.db)
        self.memory_repo = MemoryRepository(self.db)

        self.dream_service = DreamingService(
            db=self.db,
            memory_repo=self.memory_repo,
            history_repo=self.history_repo,
            session_tree=self.session_tree,
            root_dir=self.root,
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_benchmark_synthetic_duplicate_heavy_dataset(self):
        print("\n--- Dreaming Mode Performance Benchmark ---")

        # 1. Populate synthetic history: 20 sessions, each with repeated rules and preferences
        total_raw_text = []
        for i in range(20):
            conv_id = f"bench_conv_{i}"
            with self.db.get_connection() as conn:
                conn.execute(
                    "INSERT INTO conversations (id, workspace_id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (conv_id, self.workspace_id, f"Benchmark Session {i}", "COMPLETED", time.time() - (20 - i) * 100, time.time() - (20 - i) * 100 + 50)
                )

            # Repeated statements
            req1 = "Always use ./mvnw when running Maven commands."
            req2 = "Eu prefiro usar SQLite em vez de PostgreSQL para armazenar o histórico local."
            dec = "Decidimos que Context retrieval uses SQLite + FTS5."

            self.session_tree.append_entry(conv_id, "USER_TURN", {"content": req1, "text": req1})
            self.session_tree.append_entry(conv_id, "USER_TURN", {"content": req2, "text": req2})
            self.session_tree.append_entry(conv_id, "DECISION", {"text": dec, "content": dec})

            total_raw_text.extend([req1, req2, dec])

        raw_corpus = "\n".join(total_raw_text)
        tokens_before = TokenCounter.count_tokens(raw_corpus)

        # 2. Run Dreaming cycle and measure execution times
        t0 = time.perf_counter()
        snapshot = self.dream_service.orient_phase.orient(self.workspace_id)
        t_orient = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        signals = self.dream_service.gather_phase.gather(snapshot)
        t_gather = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        plan = self.dream_service.consolidate_phase.consolidate(snapshot, signals)
        t_consolidate = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        accepted, rejected = self.dream_service.validator.validate_plan(plan, snapshot)
        t_validate = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        result = self.dream_service.dream(self.workspace_id, dry_run=False)
        t_total = (time.perf_counter() - t0) * 1000.0

        # 3. Measure consolidated active memory tokens
        active_mems = self.memory_repo.get_active_memories(self.workspace_id)
        consolidated_corpus = "\n".join(m.content for m in active_mems)
        tokens_after = TokenCounter.count_tokens(consolidated_corpus)

        reduction_pct = ((tokens_before - tokens_after) / max(1, tokens_before)) * 100.0

        print(f"Sessions scanned: {result.run.sessions_scanned}")
        print(f"Signals found: {result.run.signals_found}")
        print(f"Memories added: {result.run.memories_added}")
        print(f"Memories merged: {result.run.memories_merged}")
        print(f"Raw history tokens: {tokens_before}")
        print(f"Consolidated memory tokens: {tokens_after}")
        print(f"Token reduction: {reduction_pct:.1f}%")
        print(f"Timing breakdown: Orient={t_orient:.2f}ms | Gather={t_gather:.2f}ms | Consolidate={t_consolidate:.2f}ms | Validate={t_validate:.2f}ms | Total Dream={t_total:.2f}ms")

        self.assertGreater(tokens_before, tokens_after)
        self.assertGreaterEqual(result.run.memories_added, 2)


if __name__ == "__main__":
    unittest.main()
