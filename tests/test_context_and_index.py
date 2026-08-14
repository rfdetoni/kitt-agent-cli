import subprocess
import tempfile
import unittest
import os
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
from kitt.context_engine.indexer import LocalFileIndexer
from kitt.context_engine.graph import ContextRanker

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

    def test_context_selector_applies_jaccard_redundancy_penalty(self):
        first = ContextCandidate(
            "first", "file", "a.py", 1, 20, "h1", 100, 1.0, 1.0, 1.0, False,
            "WORKSPACE", (), "first", content="alpha beta gamma delta epsilon"
        )
        redundant = ContextCandidate(
            "redundant", "file", "b.py", 1, 20, "h2", 120, 0.95, 1.0, 1.0, False,
            "WORKSPACE", (), "same", content="alpha beta gamma delta epsilon"
        )

        selected, discarded = ContextSelector.select_candidates([first, redundant], max_token_budget=300)

        self.assertIn(first, selected)
        self.assertIn(redundant, discarded)

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
        reverse_expanded = graph.expand_neighborhood({"c.py"}, max_hops=2)
        self.assertIn("a.py", reverse_expanded)
        self.assertAlmostEqual(sum(scores.values()), 1.0, places=6)

    def test_repository_graph_deduplicates_edges_and_updates_weight(self):
        graph = RepositoryGraph()
        graph.add_edge("a.py", "b.py", weight=1.0)
        generation = graph.generation
        graph.add_edge("a.py", "b.py", weight=1.0)
        graph.add_edge("a.py", "b.py", weight=2.0)

        self.assertEqual(len(graph.adj["a.py"]), 1)
        self.assertEqual(len(graph.rev_adj["b.py"]), 1)
        self.assertEqual(graph.adj["a.py"][0], ("b.py", 2.0))
        self.assertEqual(graph.generation, generation + 1)

    def test_context_ranker_uses_linear_repository_graph_adapter(self):
        ranker = ContextRanker()
        scores = ranker.compute_pagerank(
            ["a.py", "b.py", "c.py"],
            {"a.py": {"b.py"}, "b.py": {"c.py"}},
        )

        self.assertEqual(set(scores), {"a.py", "b.py", "c.py"})
        self.assertAlmostEqual(sum(scores.values()), 1.0, places=6)

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

    def test_repository_index_records_versioned_metadata_and_capabilities(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".kittignore").write_text("ignored.py\n", encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True)

            stats = index.build_or_update()
            meta = index.metadata()

            self.assertEqual(meta["schema_version"], "2")
            self.assertEqual(meta["parser_registry_version"], "parser-registry-v1")
            self.assertIn("workspace_identity", meta)
            self.assertIn("capabilities", meta)
            self.assertEqual(stats["schema_version"], "2")
            self.assertIn("freshness", stats)
            self.assertIn("partial_reason", stats)
            index.close()

    def test_repository_index_uses_parser_registry_adapter_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "app.py"
            p.write_text("def registry_symbol():\n    return 1\n", encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True)
            index.build_or_update()

            row = index._conn.execute("SELECT parser_version FROM files WHERE path='app.py'").fetchone()

            self.assertEqual(row["parser_version"], "v1")
            self.assertTrue(index.search_text("registry symbol"))
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

    def test_repository_index_records_python_qualified_names_and_end_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "app.py"
            p.write_text(
                "class Service:\n"
                "    def run(self, value: int) -> int:\n"
                "        return value + 1\n",
                encoding="utf-8",
            )
            index = RepositoryIndex(tmpdir, in_memory=True)
            index.build_or_update()

            row = index.find_symbol_location("Service.run")

            self.assertIsNotNone(row)
            self.assertEqual(row["path"], "app.py")
            self.assertEqual(row["symbol"], "run")
            self.assertEqual(row["qualified_name"], "Service.run")
            self.assertEqual(row["start_line"], 2)
            self.assertEqual(row["end_line"], 3)
            index.close()

    def test_symbol_search_returns_only_indexed_symbol_range(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.py"
            path.write_text(
                "header = True\n\n"
                "def target_symbol(value):\n    return value + 1\n\n"
                "trailer = False\n",
                encoding="utf-8",
            )
            index = RepositoryIndex(tmpdir, in_memory=True)
            index.build_or_update()

            result = index.search_symbol("target_symbol")[0]

            self.assertEqual(result["start_line"], 3)
            self.assertEqual(result["end_line"], 4)
            self.assertIn("return value + 1", result["content"])
            self.assertNotIn("trailer = False", result["content"])
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

    def test_repository_index_reports_fts_error_and_uses_lexical_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "app.py"
            p.write_text("def fallback_symbol():\n    return 1\n", encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True)
            index.build_or_update()
            if not index.has_fts5:
                index.close()
                return
            index._conn.execute("DROP TABLE fts_chunks")

            results = index.search_text("fallback symbol")

            self.assertTrue(results)
            self.assertEqual(results[0]["method"], "lexical")
            self.assertIn("fts5_error", index.last_search_error)
            index.close()

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

    def test_repository_index_skips_reparse_when_only_mtime_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "stable.py"
            path.write_text("def stable_symbol():\n    return 1\n", encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True)
            first = index.build_or_update()
            os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))

            second = index.build_or_update()

            self.assertEqual(first["generation"], second["generation"])
            self.assertEqual(second["updated"], 0)
            self.assertTrue(index.search_symbol("stable_symbol"))
            index.close()

    def test_repository_index_bootstraps_explicit_paths_before_background_scan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "target.py").write_text("def target_symbol():\n    return 1\n", encoding="utf-8")
            (root / "later.py").write_text("def later_symbol():\n    return 2\n", encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True)
            self.addCleanup(index.close)

            stats = index.bootstrap_then_background(["target.py"])

            self.assertEqual(stats["state"], "PARTIAL")
            self.assertTrue(index.search_text("target symbol"))
            index.wait_for_background(timeout=5)
            meta = index.metadata()
            self.assertEqual(meta["state"], "READY")
            self.assertTrue(index.search_text("later symbol"))

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

    def test_repository_scanner_skips_binary_files_by_sample(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "text.txt").write_text("useful text\n", encoding="utf-8")
            (root / "binary.txt").write_bytes(b"abc\0def")

            paths = {p.relative_to(root).as_posix() for p in RepositoryScanner(tmpdir).scan_files()}

            self.assertIn("text.txt", paths)
            self.assertNotIn("binary.txt", paths)

    def test_repository_scanner_detects_extended_module_manifests(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "go.work").write_text("go 1.22\n", encoding="utf-8")
            service = root / "service"
            service.mkdir()
            (service / "settings.gradle.kts").write_text("pluginManagement {}\n", encoding="utf-8")
            api = root / "api"
            api.mkdir()
            (api / "Api.csproj").write_text("<Project />\n", encoding="utf-8")

            modules = RepositoryScanner(tmpdir).detect_modules()
            by_manifest = {module["manifest_path"]: module["kind"] for module in modules}

            self.assertEqual(by_manifest["./go.work"], "go")
            self.assertEqual(by_manifest["service/settings.gradle.kts"], "java")
            self.assertEqual(by_manifest["api/Api.csproj"], "dotnet")

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

    def test_context_engine_warm_query_does_not_rescan_repository(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "app.py"
            path.write_text("def warm_symbol():\n    return 42\n", encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True)
            engine = ContextEngine(repository_index=index)
            engine.get_relevant_context("warm symbol", root_dir=tmpdir)

            def fail_full_scan():
                raise AssertionError("warm query unexpectedly rescanned workspace")

            index.build_or_update = fail_full_scan
            blocks = engine.get_relevant_context("explain warm symbol", root_dir=tmpdir)

            self.assertTrue(blocks)
            self.assertIn("warm_symbol", blocks[0].content)
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

    def test_local_file_indexer_delegates_to_repository_index_without_json_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "app.py"
            p.write_text("def adapter_symbol():\n    return 42\n", encoding="utf-8")
            indexer = LocalFileIndexer(tmpdir, persistence_enabled=False)

            tags = indexer.scan()

            self.assertTrue(any(tag.name == "adapter_symbol" for file_tags in tags for tag in file_tags.tags))
            self.assertFalse((Path(tmpdir) / ".kitt" / "cache" / "index_cache.json").exists())
            indexer.close()

    def test_query_plan_and_context_compiler_quality_gate(self):
        plan = QueryPlanner.plan("Corrija `useful_symbol` em app.py", explicit_files={"app.py"}, token_budget=500)
        cand = ContextCandidate(
            "c1", "file", "app.py", 1, 2, "hash", 20, 1.0, 1.0, 1.0,
            True, "WORKSPACE_DATA", (), "explicit target", content="def useful_symbol():\n    return 42\n"
        )

        compiled = ContextCompiler().compile(plan, [cand], [])

        self.assertTrue(compiled.quality.ok)
        self.assertEqual(compiled.quality.coverage, 1.0)
        self.assertIn("ok=true", compiled.text)
        self.assertIn("[P1] app.py", compiled.text)
        self.assertIn("def useful_symbol", compiled.text)

    def test_context_compiler_marks_missing_explicit_requirements_not_ok(self):
        plan = QueryPlanner.plan(
            "Corrija `missing_symbol` em app.py e missing.py",
            explicit_files={"app.py", "missing.py"},
            token_budget=500,
        )
        cand = ContextCandidate(
            "c1", "file", "app.py", 1, 2, "hash", 20, 1.0, 1.0, 1.0,
            True, "WORKSPACE_DATA", (), "explicit target", content="def other_symbol():\n    return 42\n"
        )

        compiled = ContextCompiler().compile(plan, [cand], [])

        self.assertFalse(compiled.quality.ok)
        self.assertTrue(compiled.quality.degraded)
        self.assertIn("ok=false", compiled.text)
        self.assertIn("not_found:path:missing.py", compiled.text)
        self.assertIn("not_found:symbol:missing_symbol", compiled.text)

    def test_query_plan_does_not_treat_sentence_words_or_acronyms_as_symbols(self):
        plan = QueryPlanner.plan("Crie um HTML explicando este projeto.")

        self.assertEqual(plan.exact_symbols, ())
        self.assertIn("html", plan.lexical_terms)

    def test_query_plan_extracts_traceback_paths_as_exact_paths(self):
        prompt = 'Traceback:\n  File "app.py", line 42, in run\nValueError: bad input\n'
        plan = QueryPlanner.plan(prompt)

        self.assertIn("app.py", plan.exact_paths)
        self.assertTrue(plan.diagnostics)

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

    def test_retrieval_keeps_large_explicit_file_as_truncated_slice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "large.py").write_text("def target():\n    pass\n" + ("x = 1\n" * 1000), encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True)
            index.build_or_update()

            selected = HybridRetrievalPipeline(index).retrieve(
                "fix large.py",
                explicit_files={"large.py"},
                max_tokens=200,
            )

            self.assertTrue(selected)
            self.assertEqual(selected[0].path, "large.py")
            self.assertEqual(selected[0].representation, "TARGETED_SLICE")
            self.assertIn("[truncated explicit file]", selected[0].content)
            index.close()

    def test_retrieval_includes_working_set_path_without_lexical_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "recent.py").write_text("def recent_context():\n    return 42\n", encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True)
            index.build_or_update()

            selected = HybridRetrievalPipeline(index).retrieve(
                "continue previous task",
                max_tokens=1000,
                working_set_paths={"recent.py"},
            )

            self.assertTrue(selected)
            self.assertEqual(selected[0].path, "recent.py")
            self.assertIn("working set", selected[0].selection_reason.lower())
            index.close()

    def test_retrieval_includes_git_status_focus(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=tmpdir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            (root / "changed.py").write_text("def changed_context():\n    return 42\n", encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True)
            index.build_or_update()

            selected = HybridRetrievalPipeline(index).retrieve("analise arquivos alterados no git", max_tokens=1000)

            self.assertTrue(selected)
            self.assertEqual(selected[0].path, "changed.py")
            self.assertEqual(selected[0].selection_reason, "Git status focus")
            index.close()

    def test_retrieval_includes_paired_tests_when_requested(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "app.py").write_text("def calculate_total():\n    return 42\n", encoding="utf-8")
            (root / "test_app.py").write_text("def test_calculate_total():\n    assert calculate_total() == 42\n", encoding="utf-8")
            index = RepositoryIndex(tmpdir, in_memory=True)
            index.build_or_update()

            selected = HybridRetrievalPipeline(index).retrieve("add tests for `calculate_total`", max_tokens=1000)
            paths = {candidate.path for candidate in selected}

            self.assertIn("app.py", paths)
            self.assertIn("test_app.py", paths)
            index.close()

if __name__ == "__main__":
    unittest.main()
