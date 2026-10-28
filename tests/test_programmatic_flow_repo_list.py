"""Regressions for directory discovery through bounded read-only programmatic tools."""
import tempfile
import unittest
from pathlib import Path

from kitt.runtime.safe_runtime import SafeRuntime
from kitt.security.capabilities import CAP_REPO_READ


class FlowRepoListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "analysis").mkdir()
        (root / "analysis" / "baseline.md").write_text("baseline\\n")
        (root / "reverse-engineering").mkdir()
        (root / "reverse-engineering" / "inventory.json").write_text("{}\\n")
        self.runtime = SafeRuntime(root, "workspace", "conversation")

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _steps():
        return [
            {"id": "analysis", "operation": "repo.list", "arguments": {"path": "analysis"}},
            {"id": "reverse", "operation": "repo.list", "arguments": {"path": "reverse-engineering"}},
        ]

    def test_parallel_flow_lists_independent_directories(self):
        result = self.runtime.execute(
            "flow.execute",
            {"steps": self._steps(), "parallel": True,
             "return": ["$analysis", "$reverse"], "max_tokens": 1200},
            effective_capabilities={CAP_REPO_READ},
        )
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.data["tool_calls"], 2)
        self.assertEqual(
            [entry["path"] for entry in result.data["result"]["analysis"]["entries"]],
            ["analysis/baseline.md"],
        )
        self.assertEqual(
            [entry["path"] for entry in result.data["result"]["reverse"]["entries"]],
            ["reverse-engineering/inventory.json"],
        )

    def test_program_runtime_can_list_with_same_capability(self):
        result = self.runtime.execute(
            "program.execute",
            {"program": [
                {"call": "repo.list", "arguments": {"path": "analysis"}, "save": "listing"},
                {"return": "$listing"},
            ]},
            effective_capabilities={CAP_REPO_READ},
        )
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.data["result"]["entries"][0]["path"], "analysis/baseline.md")

    def test_missing_read_capability_is_not_bypassed(self):
        for operation, arguments in (
            ("flow.execute", {"steps": self._steps()}),
            ("program.execute", {"program": [
                {"call": "repo.list", "arguments": {"path": "analysis"}},
            ]}),
        ):
            with self.subTest(operation=operation):
                result = self.runtime.execute(operation, arguments, effective_capabilities=set())
                self.assertFalse(result.success)
                self.assertIn(CAP_REPO_READ, result.error)

    def test_mutation_remains_disallowed(self):
        result = self.runtime.execute(
            "flow.execute",
            {"steps": [{
                "id": "write", "operation": "repo.write_file",
                "arguments": {"path": "danger.txt", "content": "not allowed"},
            }]},
            effective_capabilities={CAP_REPO_READ},
        )
        self.assertFalse(result.success)
        self.assertFalse((Path(self.tmp.name) / "danger.txt").exists())


if __name__ == "__main__":
    unittest.main()
