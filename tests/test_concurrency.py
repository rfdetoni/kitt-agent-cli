import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from kitt.history.database import HistoryDatabase
from kitt.tools.approval import ApprovalManager
from kitt.index.repository import RepositoryIndex


class TestConcurrentApprovals(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = HistoryDatabase(self.tmp.name)
        self.manager = ApprovalManager(ttl_seconds=60.0, db=self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_concurrent_grants_cannot_double_consume(self):
        req = self.manager.register_request(
            turn_id="turn_c1",
            conversation_id="conv_c1",
            workspace_id="ws_c1",
            action_hash="hash_c1",
            approval_id="appr_c1",
            tool_name="apply_patch",
        )
        grant = self.manager.issue_grant(
            turn_id="turn_c1",
            conversation_id="conv_c1",
            workspace_id="ws_c1",
            action_hash="hash_c1",
            approval_id="appr_c1",
        )
        self.assertIsNotNone(grant)

        results = []
        def try_consume():
            return self.manager.validate_and_consume(
                grant=grant,
                expected_action_hash="hash_c1",
                expected_turn_id="turn_c1",
                expected_conv_id="conv_c1",
                expected_ws_id="ws_c1",
                expected_approval_id="appr_c1",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(try_consume) for _ in range(16)]
            results = [f.result() for f in futures]

        # Exactly one thread succeeds in CAS consumption
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 15)


class TestConcurrentIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for i in range(10):
            (self.root / f"file_{i}.py").write_text(f"def func_{i}(): pass\n", encoding="utf-8")
        self.index = RepositoryIndex(self.tmp.name, in_memory=True)

    def tearDown(self):
        self.index.close()
        self.tmp.cleanup()

    def test_concurrent_update_paths(self):
        def worker(idx):
            p = f"file_{idx}.py"
            (self.root / p).write_text(f"def func_{idx}_updated(): return {idx}\n", encoding="utf-8")
            return self.index.update_paths([p])

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker, i) for i in range(10)]
            results = [f.result() for f in futures]

        for res in results:
            self.assertIsInstance(res, dict)
        self.assertGreater(self.index.index_generation(), 0)


class TestConcurrentHistoryDatabase(unittest.TestCase):
    def test_concurrent_history_in_memory_transactions_are_serialized(self):
        db = HistoryDatabase(":memory:", in_memory=True)
        barrier = concurrent.futures.ThreadPoolExecutor(max_workers=8)

        def worker(thread_idx):
            with db.get_connection() as conn:
                conn.execute(
                    "INSERT INTO workspaces(id, canonical_path_hash, display_name, git_root, created_at, last_opened_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (f"ws_{thread_idx}", f"hash_{thread_idx}", f"name_{thread_idx}", None, 1.0, 1.0)
                )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(worker, i) for i in range(20)]
            for f in futures:
                f.result()

        with db.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]
        self.assertEqual(count, 20)
        db.close()


class TestMasterPromptHardening(unittest.TestCase):
    def test_fetch_ollama_models_does_not_invent_models_when_offline(self):
        from kitt.router.model_selector import ModelConfigurator
        cfg = ModelConfigurator()
        # Invalid / unreachable port
        models = cfg.fetch_ollama_models("http://127.0.0.1:59999")
        self.assertEqual(models, [])

    def test_deterministic_summary_preserves_signals(self):
        from kitt.compaction.service import CompactionService
        raw = "\n".join([f"line {i}" for i in range(100)])
        raw += "\nERROR: Database connection timed out\n"
        raw += "\n".join([f"more {i}" for i in range(50)])
        raw += "\nDecision: Use SQLite with WAL\n"

        summary = CompactionService._deterministic_summary(raw)
        self.assertIn("ERROR: Database connection timed out", summary)
        self.assertIn("Decision: Use SQLite with WAL", summary)
        self.assertIn("[... compacted ...]", summary)

    def test_progressive_skill_loader_tfidf_ranking(self):
        from kitt.skills.loader import ProgressiveSkillLoader
        from dataclasses import dataclass
        @dataclass
        class DummySkill:
            name: str
            description: str
            path: any = None

        skills = [
            DummySkill("python-expert", "Expert Python coding and debugging patterns"),
            DummySkill("git-workflow", "Git commit, branching, and rebasing techniques"),
            DummySkill("docker-deploy", "Docker containerization and orchestration"),
        ]
        loader = ProgressiveSkillLoader()
        selected = loader.select(skills, "Please debug my python code with python-expert")
        self.assertEqual(selected[0].name, "python-expert")

    def test_cost_estimator_hierarchical_pricing(self):
        from kitt.metrics.cost_estimator import estimate_cost, TurnCost
        cost = estimate_cost("gpt-4o", input_tokens=1000, output_tokens=500)
        self.assertIsInstance(cost, TurnCost)
        self.assertGreater(cost.estimated_usd, 0.0)


if __name__ == "__main__":
    unittest.main()
