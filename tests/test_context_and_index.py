import tempfile
import unittest
from pathlib import Path

from kitt.context.token_estimator import CalibratedTokenEstimator
from kitt.context.candidates import ContextCandidate, ContextSelector
from kitt.context.compiler import ContextCompiler
from kitt.context.query_plan import QueryPlanner
from kitt.context.retrieval import HybridRetrievalPipeline
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

    def test_context_selector_includes_dependencies_and_deduplicates_ranges(self):
        target = ContextCandidate("target", "file", "a.py", 10, 20, "h1", 100, 1.0, 1.0, 1.0, True, "USER", ("dep",), "target")
        dep = ContextCandidate("dep", "file", "b.py", 1, 5, "h2", 50, 0.8, 1.0, 1.0, False, "WORKSPACE", (), "dependency")
        duplicate = ContextCandidate("dup", "file", "a.py", 10, 20, "h1", 20, 0.9, 1.0, 1.0, False, "WORKSPACE", (), "duplicate")

        selected, discarded = ContextSelector.select_candidates([target, dep, duplicate], max_token_budget=200)

        self.assertIn(target, selected)
        self.assertIn(dep, selected)
        self.assertIn(duplicate, discarded)

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
            self.assertEqual(stats["generation"], 1)
            self.assertEqual(stats["state"], "READY")

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

    def test_repository_index_persistent_fts_survives_reopen(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "app.py"
            p.write_text("def persistent_symbol():\n    return 1\n", encoding="utf-8")

            first = RepositoryIndex(tmpdir)
            first.build_or_update()
            first.close()

            reopened = RepositoryIndex(tmpdir)
            results = reopened.search_text("persistent symbol")

            self.assertTrue(results)
            if reopened.has_fts5:
                self.assertEqual(results[0]["method"], "fts5")
            reopened.close()

    def test_repository_index_update_paths_refreshes_single_changed_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "app.py"
            p.write_text("def before_symbol():\n    return 1\n", encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True)
            index.build_or_update()

            p.write_text("def after_symbol():\n    return 2\n", encoding="utf-8")
            stats = index.update_paths(["app.py"])

            self.assertEqual(stats["updated"], 1)
            self.assertFalse(index.search_text("before symbol"))
            self.assertTrue(index.search_text("after symbol"))
            index.close()

    def test_repository_scanner_respects_kittignore(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".kittignore").write_text("ignored.py\nsecret_dir\n", encoding="utf-8")
            (root / "kept.py").write_text("def kept(): pass\n", encoding="utf-8")
            (root / "ignored.py").write_text("def ignored(): pass\n", encoding="utf-8")
            (root / "secret_dir").mkdir()
            (root / "secret_dir" / "hidden.py").write_text("def hidden(): pass\n", encoding="utf-8")

            paths = {p.relative_to(root).as_posix() for p in RepositoryScanner(tmpdir).scan_files()}

            self.assertIn("kept.py", paths)
            self.assertNotIn("ignored.py", paths)
            self.assertNotIn("secret_dir/hidden.py", paths)

    def test_repository_index_respects_file_size_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "small.py").write_text("def small_symbol(): pass\n", encoding="utf-8")
            (root / "large.py").write_text("def large_symbol():\n    pass\n" + ("x = 1\n" * 200), encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True, max_file_bytes=64)

            stats = index.build_or_update()

            self.assertEqual(stats["scanned"], 1)
            self.assertTrue(index.search_text("small symbol"))
            self.assertFalse(index.search_text("large symbol"))
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
            self.assertIn("## Context v1", blocks[0].content)
            self.assertEqual(engine.last_compiled_context.selected_count, 1)
            self.assertFalse((Path(tmpdir) / ".kitt" / "cache" / "index_cache.json").exists())
            index.close()

    def test_context_engine_without_supplied_index_uses_repository_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "app.py"
            p.write_text("def lazy_symbol():\n    return 42\n", encoding="utf-8")
            engine = ContextEngine(persistence_enabled=False)

            blocks = engine.get_relevant_context("lazy symbol", root_dir=tmpdir)

            self.assertIsInstance(engine.index, RepositoryIndex)
            self.assertTrue(blocks)
            self.assertIn("lazy_symbol", blocks[0].content)
            self.assertFalse((Path(tmpdir) / ".kitt" / "cache" / "index_cache.json").exists())
            engine.index.close()

    def test_query_plan_and_context_compiler_quality_gate(self):
        plan = QueryPlanner.plan("Corrija `useful_symbol` em app.py", explicit_files={"app.py"}, token_budget=500)
        cand = ContextCandidate(
            "c1", "file", "app.py", 1, 2, "hash", 20, 1.0, 1.0, 1.0,
            True, "WORKSPACE_DATA", (), "explicit target", content="def useful_symbol():\n    return 42\n"
        )

        compiled = ContextCompiler().compile(plan, [cand], [])

        self.assertTrue(compiled.quality.ok)
        self.assertEqual(compiled.quality.coverage, 1.0)
        self.assertIn("[P1] app.py", compiled.text)
        self.assertIn("def useful_symbol", compiled.text)

    def test_query_plan_does_not_treat_sentence_words_or_acronyms_as_symbols(self):
        plan = QueryPlanner.plan("Crie um HTML explicando este projeto.")

        self.assertEqual(plan.exact_symbols, ())
        self.assertIn("html", plan.lexical_terms)

    def test_repository_index_removes_deleted_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "gone.py"
            p.write_text("def removed_symbol():\n    return 1\n", encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True)
            index.build_or_update()
            self.assertTrue(index.search_text("removed symbol"))

            p.unlink()
            stats = index.build_or_update()
            results = index.search_text("removed symbol")

            self.assertEqual(stats["deleted"], 1)
            self.assertEqual(results, [])
            index.close()

    def test_repository_index_links_modules_and_reference_edges(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            (root / "controller.py").write_text(
                "from service import UserService\nclass Controller:\n    def run(self):\n        return UserService()\n",
                encoding="utf-8",
            )
            (root / "service.py").write_text("class UserService:\n    pass\n", encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True)

            index.build_or_update()

            modules = index._conn.execute("SELECT COUNT(*) FROM modules").fetchone()[0]
            file_modules = index._conn.execute("SELECT COUNT(*) FROM files WHERE module_id IS NOT NULL").fetchone()[0]
            edges = index._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            expanded = index.graph.expand_neighborhood({"controller.py"}, max_hops=1)

            self.assertGreaterEqual(modules, 1)
            self.assertEqual(file_modules, 3)
            self.assertGreaterEqual(edges, 1)
            self.assertIn("service.py", expanded)
            index.close()

    def test_retrieval_expands_graph_neighbors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "controller.py").write_text(
                "from service import UserService\nclass Controller:\n    def run(self):\n        return UserService()\n",
                encoding="utf-8",
            )
            (root / "service.py").write_text("class UserService:\n    pass\n", encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True)
            index.build_or_update()

            selected = HybridRetrievalPipeline(index).retrieve("explain Controller", max_tokens=1000)
            paths = {candidate.path for candidate in selected}

            self.assertIn("controller.py", paths)
            self.assertIn("service.py", paths)
            index.close()

    def test_retrieval_prefers_exact_symbol_before_lexical(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "alpha.py").write_text("def target_symbol():\n    return 1\n", encoding="utf-8")
            (root / "notes.md").write_text("target_symbol mentioned in prose\n", encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True)
            index.build_or_update()

            selected = HybridRetrievalPipeline(index).retrieve("fix `target_symbol`", max_tokens=1000)

            self.assertEqual(selected[0].path, "alpha.py")
            self.assertIn("Exact symbol match", selected[0].selection_reason)
            index.close()

if __name__ == "__main__":
    unittest.main()
