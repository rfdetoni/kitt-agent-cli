import unittest
import tempfile
from pathlib import Path
from kitt.context_engine.engine import ContextEngine
from kitt.core.autonomy_policy import AutonomyPolicy
from kitt.index.repository import RepositoryIndex
from kitt.tools.registry import ToolRegistry

class TestToolRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp_dir.name).resolve()
        self.registry = ToolRegistry(root_dir=self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_tool_definitions_filtering(self):
        defs = self.registry.get_tool_definitions(enabled_tools=["read_file", "git_status"])
        names = [d["name"] for d in defs]
        self.assertIn("read_file", names)
        self.assertIn("git_status", names)
        self.assertNotIn("apply_patch", names)

    def test_execute_tool_disabled_rejection(self):
        res = self.registry.execute_tool("apply_patch", {}, enabled_tools=["read_file"])
        self.assertFalse(res.success)
        self.assertIn("not enabled", res.error)

    def test_execute_tool_policy_denial(self):
        res = self.registry.execute_tool("run_command", {"command": "rm -rf /"}, enabled_tools=["run_command"])
        self.assertFalse(res.success)
        self.assertIn("denied by PolicyEngine", res.error)

    def test_read_file_tool(self):
        f = self.root_path / "sample.py"
        f.write_text("line1\nline2\nline3\n", encoding='utf-8')

        res = self.registry.execute_tool("read_file", {"path": "sample.py", "start_line": 1, "end_line": 2}, enabled_tools=["read_file"])
        self.assertTrue(res.success)
        self.assertEqual(res.output, "line1\nline2")
        self.assertEqual(res.metadata["hash_scope"], "returned_range")
        self.assertEqual(res.metadata["end_line"], 2)

    def test_search_uses_repository_index_by_default(self):
        f = self.root_path / "sample.py"
        f.write_text("def target_symbol():\n    return 1\n", encoding='utf-8')
        index = RepositoryIndex(self.tmp_dir.name, in_memory=True)
        registry = ToolRegistry(root_dir=self.tmp_dir.name, context_engine=ContextEngine(index))

        res = registry.execute_tool("search", {"pattern": "target symbol"}, enabled_tools=["search"])

        self.assertTrue(res.success)
        self.assertEqual(res.metadata["method"], "index")
        self.assertIn("sample.py", res.output)
        index.close()

    def test_regex_search_uses_bounded_scanner_ignores_kittignore(self):
        (self.root_path / ".kittignore").write_text("ignored.py\n", encoding="utf-8")
        (self.root_path / "kept.py").write_text("def kept_match(): pass\n", encoding="utf-8")
        (self.root_path / "ignored.py").write_text("def ignored_match(): pass\n", encoding="utf-8")

        res = self.registry.execute_tool(
            "search",
            {"pattern": ".*_match", "regex": True},
            enabled_tools=["search"],
        )

        self.assertTrue(res.success)
        self.assertIn("kept.py", res.output)
        self.assertNotIn("ignored.py", res.output)

    def test_write_file_updates_repository_index(self):
        index = RepositoryIndex(self.tmp_dir.name, in_memory=True)
        registry = ToolRegistry(root_dir=self.tmp_dir.name, context_engine=ContextEngine(index))
        registry.policy.autonomy = AutonomyPolicy.preset("balanced")

        res = registry.execute_tool(
            "write_file",
            {"path": "created.py", "content": "def fresh_symbol():\n    return 1\n"},
            enabled_tools=["write_file"],
        )
        results = index.search_text("fresh symbol")

        self.assertTrue(res.success)
        self.assertTrue(results)
        self.assertEqual(results[0]["path"], "created.py")
        index.close()

    def test_write_file_expected_hash_blocks_stale_write(self):
        f = self.root_path / "sample.py"
        f.write_text("old\n", encoding='utf-8')
        self.registry.policy.autonomy = AutonomyPolicy.preset("balanced")

        res = self.registry.execute_tool(
            "write_file",
            {"path": "sample.py", "content": "new\n", "expected_content_hash": "stale"},
            enabled_tools=["write_file"],
        )

        self.assertFalse(res.success)
        self.assertIn("expected_content_hash mismatch", res.error)
        self.assertEqual(f.read_text(encoding='utf-8'), "old\n")

if __name__ == '__main__':
    unittest.main()
