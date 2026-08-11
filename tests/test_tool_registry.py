import unittest
import tempfile
from pathlib import Path
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

if __name__ == '__main__':
    unittest.main()
