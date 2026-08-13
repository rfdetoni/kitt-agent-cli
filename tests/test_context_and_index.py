import tempfile
import unittest
from pathlib import Path

from kitt.context.token_estimator import CalibratedTokenEstimator
from kitt.context.candidates import ContextCandidate, ContextSelector
from kitt.index.graph import RepositoryGraph
from kitt.index.scanner import RepositoryScanner
from kitt.index.repository import RepositoryIndex
from kitt.context_engine.engine import ContextEngine

class TestContextAndIndex(unittest.TestCase):
    def test_calibrated_token_estimator(self):
        estimator = CalibratedTokenEstimator()
        est = estimator.count_text("def foo():\n    return 42\n")
        self.assertGreater(est.count, 0)
        self.assertEqual(est.method, "calibrated_lang")
        self.assertLessEqual(est.error_margin, 0.1)

    def test_context_knapsack_selector(self):
        c1 = ContextCandidate("c1", "file", "f1.py", 1, 10, "h1", 100, 1.0, 1.0, 1.0, True, "USER", (), "mandatory")
        c2 = ContextCandidate("c2", "file", "f2.py", 1, 10, "h2", 200, 0.9, 0.9, 0.9, False, "WORKSPACE", (), "optional high val")
        c3 = ContextCandidate("c3", "file", "f3.py", 1, 10, "h3", 500, 0.5, 0.5, 0.5, False, "WORKSPACE", (), "optional low val")

        selected, discarded = ContextSelector.select_candidates([c1, c2, c3], max_token_budget=350)
        self.assertIn(c1, selected)
        self.assertIn(c2, selected)
        self.assertNotIn(c3, selected)

    def test_repository_graph_pagerank(self):
        graph = RepositoryGraph()
        graph.add_edge("a.py", "b.py")
        graph.add_edge("b.py", "c.py")

        scores = graph.compute_pagerank()
        self.assertIn("a.py", scores)
        self.assertIn("b.py", scores)

        expanded = graph.expand_neighborhood({"a.py"}, max_hops=2)
        self.assertIn("b.py", expanded)
        self.assertIn("c.py", expanded)

    def test_repository_index_incremental_update_and_search(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "app.py"
            p.write_text("def hello():\n    print('Hello World')\n", encoding="utf-8")

            index = RepositoryIndex(tmpdir, in_memory=True)
            stats = index.build_or_update()
            self.assertGreaterEqual(stats["scanned"], 1)

            results = index.search_text("Hello World")
            self.assertGreaterEqual(len(results), 1)
            index.close()

    def test_repository_index_fts_handles_natural_language_and_tail_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "app.py"
            p.write_text(("x = 1\n" * 900) + "def target_symbol():\n    return 'tail'\n", encoding="utf-8")

            index = RepositoryIndex(tmpdir, in_memory=True)
            index.build_or_update()

            results = index.search_text("fix target symbol")
            self.assertTrue(results)
            if index.has_fts5:
                self.assertEqual(results[0]["method"], "fts5")
            self.assertIn("target_symbol", results[0]["content"])

            counts = {
                table: index._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("symbols", "chunks")
            }
            self.assertGreater(counts["symbols"], 0)
            self.assertGreater(counts["chunks"], 1)
            index.close()

    def test_context_engine_uses_shared_repository_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "app.py"
            p.write_text("def useful_symbol():\n    return 42\n", encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True)
            engine = ContextEngine(repository_index=index)

            blocks = engine.get_relevant_context("useful symbol", root_dir=tmpdir)

            self.assertIs(engine.index, index)
            self.assertTrue(blocks)
            self.assertIn("useful_symbol", blocks[0].content)
            self.assertFalse((Path(tmpdir) / ".kitt" / "cache" / "index_cache.json").exists())
            index.close()

if __name__ == "__main__":
    unittest.main()
