from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kitt.context_engine.context_map import ContextMapBuilder
from kitt.runtime.programmatic_flow import ProgrammaticToolFlow
from kitt.runtime.progressive import apply_progressive_search_view
from kitt.runtime.retrieval_guard import RetrievalGuard
from kitt.runtime.safe_runtime import SafeRuntimeResult
from kitt.validation.post_edit import PostEditValidator


class RetrievalGuardTests(unittest.TestCase):
    def test_repeated_unchanged_read_collapses_to_stub(self):
        guard = RetrievalGuard()
        first = SafeRuntimeResult(
            True,
            "repo.read",
            data="x" * 4000,
            context_handles=["ctx:file:a.py:1-100"],
            metadata={"content_hash": "abc"},
        )
        self.assertIs(guard.observe("repo.read", {"path": "a.py"}, first), first)

        second = SafeRuntimeResult(
            True,
            "repo.read",
            data="x" * 4000,
            context_handles=["ctx:file:a.py:1-100"],
            metadata={"content_hash": "abc"},
        )
        result = guard.observe("repo.read", {"path": "a.py"}, second)
        self.assertTrue(result.data["deduplicated"])
        self.assertGreater(result.tokens_saved, 0)

    def test_changed_fingerprint_is_not_hidden(self):
        guard = RetrievalGuard()
        guard.observe(
            "repo.read",
            {"path": "a.py"},
            SafeRuntimeResult(True, "repo.read", data="old", metadata={"content_hash": "old"}),
        )
        changed = guard.observe(
            "repo.read",
            {"path": "a.py"},
            SafeRuntimeResult(True, "repo.read", data="new", metadata={"content_hash": "new"}),
        )
        self.assertEqual(changed.data, "new")


class ProgressiveSearchTests(unittest.TestCase):
    def test_default_search_view_returns_files_not_snippets(self):
        original = SafeRuntimeResult(
            True,
            "repo.search",
            data={
                "hits": [
                    {"path": "a.py", "line": 2, "text": "secret huge snippet", "score": 1},
                    {"path": "a.py", "line": 8, "text": "another", "score": 1},
                    {"path": "b.py", "line": 1, "text": "match", "score": 1},
                ],
                "omitted_matches": 0,
            },
        )
        result = apply_progressive_search_view(original, {"query": "needle"})
        self.assertEqual(result.data["view"], "files")
        self.assertEqual(result.data["files"][0]["path"], "a.py")
        self.assertNotIn("secret huge snippet", json.dumps(result.data))
        self.assertGreater(result.tokens_saved, 0)


class FlowTests(unittest.TestCase):
    def test_flow_resolves_refs_and_hides_intermediate_payloads(self):
        class Runtime:
            def execute(self, operation, arguments, **kwargs):
                if operation == "repo.search":
                    return SafeRuntimeResult(
                        True,
                        operation,
                        data={"files": [{"path": "src/a.py"}], "bulk": "x" * 4000},
                    )
                if operation == "repo.read":
                    self.last_path = arguments["path"]
                    return SafeRuntimeResult(True, operation, data={"summary": "ok"})
                raise AssertionError(operation)

        runtime = Runtime()
        flow = ProgrammaticToolFlow(runtime)
        result = flow.execute(
            {
                "steps": [
                    {"id": "search", "operation": "repo.search", "arguments": {"query": "x"}},
                    {
                        "id": "read",
                        "operation": "repo.read",
                        "arguments": {"path": "$search.files.0.path"},
                    },
                ],
                "return": "$read.summary",
                "max_tokens": 128,
            },
            turn_id="t",
            origin="MODEL",
            capabilities=set(),
            security_context=None,
        )
        self.assertTrue(result.success)
        self.assertEqual(runtime.last_path, "src/a.py")
        self.assertEqual(result.data["result"], "ok")
        self.assertTrue(result.data["intermediate_payloads_hidden"])
        self.assertNotIn("x" * 200, json.dumps(result.data))
        self.assertGreater(result.tokens_saved, 0)

    def test_flow_foreach_reads_ranked_files_without_exposing_each_result(self):
        class Runtime:
            def __init__(self):
                self.paths = []

            def execute(self, operation, arguments, **kwargs):
                if operation == "repo.search":
                    return SafeRuntimeResult(
                        True,
                        operation,
                        data={"files": [{"path": "a.py"}, {"path": "b.py"}]},
                    )
                if operation == "repo.read":
                    self.paths.append(arguments["path"])
                    return SafeRuntimeResult(
                        True, operation, data={"path": arguments["path"], "body": "x" * 500}
                    )
                raise AssertionError(operation)

        runtime = Runtime()
        result = ProgrammaticToolFlow(runtime).execute(
            {
                "steps": [
                    {
                        "id": "search",
                        "operation": "repo.search",
                        "arguments": {"query": "needle"},
                    },
                    {
                        "id": "reads",
                        "foreach": "$search.files",
                        "as": "file",
                        "operation": "repo.read",
                        "arguments": {"path": "$file.path"},
                        "limit": 2,
                    },
                ],
                "return": "$reads",
                "max_tool_calls": 4,
                "max_tokens": 256,
            },
            turn_id="t",
            origin="MODEL",
            capabilities=set(),
            security_context=None,
        )
        self.assertTrue(result.success)
        self.assertEqual(runtime.paths, ["a.py", "b.py"])
        self.assertEqual(result.data["tool_calls"], 3)

    def test_flow_refuses_mutating_operations(self):
        flow = ProgrammaticToolFlow(SimpleNamespace())
        result = flow.execute(
            {"steps": [{"operation": "patch.apply", "arguments": {"patch": "x"}}]},
            turn_id="t",
            origin="MODEL",
            capabilities=set(),
            security_context=None,
        )
        self.assertFalse(result.success)
        self.assertIn("read-only", result.error)


class ContextMapTests(unittest.TestCase):
    def test_goal_and_git_changes_boost_context(self):
        class Index:
            def search_text(self, term, limit=24):
                return [
                    {"path": "src/auth.py", "content": term, "score": 1.0},
                    {"path": "src/util.py", "content": term, "score": 0.5},
                ]

        class Goals:
            def active(self, conversation_id):
                return SimpleNamespace(
                    id="goal_1",
                    objective="Fix AuthenticationProvider refresh",
                    success_criteria=["auth tests pass"],
                )

        class Runner:
            def run(self, argv, timeout_seconds=5):
                return SimpleNamespace(returncode=0, stdout=" M src/auth.py\n", stderr="")

        registry = SimpleNamespace(process_runner=Runner())
        result = ContextMapBuilder(Index(), Goals(), registry).build(
            {}, conversation_id="c", security_context=None
        )
        self.assertEqual(result["goal_id"], "goal_1")
        self.assertEqual(result["files"][0]["path"], "src/auth.py")
        self.assertIn("git_changed", result["files"][0]["reasons"])


class PostEditGateTests(unittest.TestCase):
    def test_invalid_python_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bad.py").write_text("def broken(:\n", encoding="utf-8")
            report = PostEditValidator(root).validate_paths(["bad.py"])
            self.assertFalse(report.ok)
            self.assertEqual(report.checked, 1)
            self.assertEqual(report.diagnostics[0].validator, "python.ast")

    def test_valid_json_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ok.json").write_text('{"ok": true}', encoding="utf-8")
            report = PostEditValidator(root).validate_paths(["ok.json"])
            self.assertTrue(report.ok)
            self.assertEqual(report.checked, 1)


if __name__ == "__main__":
    unittest.main()
