import tempfile
import unittest
from pathlib import Path

from kitt.context_filter.fallback import DeterministicFallbackPlanner
from kitt.runtime.safe_runtime import OPERATION_SPECS
from kitt.security.workspace_fs import WorkspaceFileSystem


class DirectoryCreationRegressionTests(unittest.TestCase):
    def test_exact_portuguese_prompt_is_mutating_and_exposes_native_tool(self):
        planner = DeterministicFallbackPlanner()
        task = planner.generate_task("crie uma pasta chamada testePasta")
        self.assertEqual(task.intent, "IMPLEMENT")
        self.assertIn("create_directory", planner.generate_plan(task).enabled_tools)

    def test_workspace_filesystem_creates_directory_without_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fs = WorkspaceFileSystem(root)
            relative = fs.create_directory("testePasta")
            self.assertEqual(relative, "testePasta")
            self.assertTrue((root / "testePasta").is_dir())
            self.assertEqual(fs.create_directory("testePasta"), "testePasta")

    def test_directory_creation_rejects_escape_and_protected_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            fs = WorkspaceFileSystem(temp)
            with self.assertRaises(PermissionError):
                fs.create_directory("../fora")
            with self.assertRaises(PermissionError):
                fs.create_directory(".git/hooks")

    def test_safe_runtime_exposes_directory_operation(self):
        spec = OPERATION_SPECS["repo.create_directory"]
        self.assertEqual(spec.policy_tool_action, "create_directory")
        self.assertEqual(spec.resume_tool_name, "create_directory")


if __name__ == "__main__":
    unittest.main()
