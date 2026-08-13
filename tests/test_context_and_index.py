import tempfile
import unittest
from pathlib import Path

from kitt.context.token_estimator import CalibratedTokenEstimator
from kitt.context.candidates import ContextCandidate, ContextSelector
from kitt.index.graph import RepositoryGraph
from kitt.index.scanner import RepositoryScanner
from kitt.index.repository import RepositoryIndex

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

if __name__ == "__main__":
    unittest.main()
