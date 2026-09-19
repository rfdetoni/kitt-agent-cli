import sys
import unittest
import tempfile
from pathlib import Path
from kitt.context_engine.engine import ContextEngine
from kitt.core.autonomy_policy import AutonomyPolicy
from kitt.index.repository import RepositoryIndex
from kitt.tools.registry import ToolRegistry

class TestToolRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root_path = Path(self.tmp_dir.name).resolve()
        self.registry = ToolRegistry(root_dir=self.tmp_dir.name)

    def tearDown(self):
        self.registry.close()
        self.tmp_dir.cleanup()

    def test_tool_definitions_filtering(self):
        defs = self.registry.get_tool_definitions(enabled_tools=["read_file", "git_status"])
        names = [d["name"] for d in defs]
        self.assertIn("read_file", names)
        self.assertIn("git_status", names)
        self.assertNotIn("apply_patch", names)
        self.assertIn("path", defs[names.index("read_file")]["args"])

    def test_runtime_definition_forbids_shell_file_mutation(self):
        defs = self.registry.get_tool_definitions(enabled_tools=["kitt_runtime"])
        runtime = next(item for item in defs if item["name"] == "kitt_runtime")
        description = runtime.get("description", "")
        args_description = runtime.get("args", {}).get("arguments", {}).get("description", "")

        self.assertIn("file edits use repo.write_file or patch.apply", description)
        self.assertIn(
            "process.run {argv:[...],cwd?,timeout_seconds?,network?:bool=false}",
            description,
        )
        self.assertIn("process.run never accepts command/cmd/args", args_description)

    def test_execute_tool_disabled_rejection(self):
        res = self.registry.execute_tool("apply_patch", {}, enabled_tools=["read_file"])
        self.assertFalse(res.success)
        self.assertIn("not enabled", res.error)

    def test_execute_tool_policy_denial(self):
        res = self.registry.execute_tool(
            "run_command",
            {"argv": ["rm", "-rf", "/"]},
            enabled_tools=["run_command"],
        )
        self.assertFalse(res.success)
        self.assertIn("denied by PolicyEngine", res.error)

    def test_run_command_rejects_legacy_command_contract(self):
        res = self.registry.execute_tool(
            "run_command",
            {"command": "pwd"},
            enabled_tools=["run_command"],
        )
        self.assertFalse(res.success)
        self.assertIn("denied by PolicyEngine", res.error)

    def test_run_command_uses_argv_and_workspace_cwd(self):
        workdir = self.root_path / "frontend"
        workdir.mkdir()
        self.registry.policy.autonomy = AutonomyPolicy.preset("autonomous")

        res = self.registry.execute_tool(
            "run_command",
            {
                "argv": [
                    sys.executable,
                    "-c",
                    "import os; print(os.path.basename(os.getcwd()))",
                ],
                "cwd": "frontend",
                "timeout_seconds": 30,
            },
            enabled_tools=["run_command"],
        )

        if self.registry.process_runner.sandbox.is_strong_available():
            self.assertTrue(res.success, res.error)
            self.assertEqual(res.output.strip(), "frontend")
            self.assertTrue(res.metadata["sandbox"]["strong"])
            self.assertTrue(res.metadata["sandbox"]["network_isolated"])
        else:
            self.assertFalse(res.success)
            self.assertTrue(res.requires_approval)

    def test_run_command_network_requires_explicit_elevation(self):
        self.registry.policy.autonomy = AutonomyPolicy.preset("autonomous")
        args = {
            "argv": [sys.executable, "-c", "print('network-approved')"],
            "network": True,
        }
        turn_id = "turn-network"
        conversation_id = "conv-network"
        workspace_id = "ws-network"

        pending = self.registry.execute_tool(
            "run_command",
            args,
            turn_id=turn_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            enabled_tools=["run_command"],
        )
        self.assertFalse(pending.success)
        self.assertTrue(pending.requires_approval)
        self.assertEqual(
            pending.metadata["network"]["required_capability"],
            "network.access",
        )

        action_hash = self.registry.policy.generate_action_hash("run_command", args)
        approval_id = "approval-network"
        self.registry.approval_manager.register_request(
            turn_id,
            conversation_id,
            workspace_id,
            action_hash,
            approval_id,
            tool_name="run_command",
        )
        grant = self.registry.approval_manager.issue_grant(
            turn_id,
            conversation_id,
            workspace_id,
            action_hash,
            approval_id=approval_id,
        )
        self.assertIsNotNone(grant)

        approved = self.registry.execute_tool(
            "run_command",
            args,
            turn_id=turn_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            enabled_tools=["run_command"],
            grant=grant,
            expected_approval_id=approval_id,
        )
        self.assertTrue(approved.success, approved.error)
        self.assertEqual(approved.output.strip(), "network-approved")
        self.assertEqual(
            approved.metadata["sandbox"]["profile"],
            "workspace-write+network",
        )

    def test_run_command_rejects_non_boolean_network(self):
        self.registry.policy.autonomy = AutonomyPolicy.preset("autonomous")
        res = self.registry.execute_tool(
            "run_command",
            {
                "argv": [sys.executable, "-c", "print('never')"],
                "network": "true",
            },
            enabled_tools=["run_command"],
        )
        self.assertFalse(res.success)
        self.assertIn("network must be a boolean", res.error)

    def test_control_plane_write_requires_exact_single_use_approval(self):
        control_dir = self.root_path / ".kitt" / "security"
        control_dir.mkdir(parents=True)
        self.registry.policy.autonomy = AutonomyPolicy.preset("autonomous")
        args = {
            "path": ".kitt/security/policy.txt",
            "content": "policy=true\n",
        }
        turn_id = "turn-control"
        conversation_id = "conv-control"
        workspace_id = "ws-control"

        pending = self.registry.execute_tool(
            "write_file",
            args,
            turn_id=turn_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            enabled_tools=["write_file"],
        )
        self.assertFalse(pending.success)
        self.assertTrue(pending.requires_approval)
        self.assertEqual(
            pending.metadata["control_plane"]["required_capability"],
            "control_plane.write",
        )
        self.assertFalse((control_dir / "policy.txt").exists())

        action_hash = self.registry.policy.generate_action_hash("write_file", args)
        approval_id = "approval-control"
        self.registry.approval_manager.register_request(
            turn_id,
            conversation_id,
            workspace_id,
            action_hash,
            approval_id,
            tool_name="write_file",
        )
        grant = self.registry.approval_manager.issue_grant(
            turn_id,
            conversation_id,
            workspace_id,
            action_hash,
            approval_id=approval_id,
        )
        self.assertIsNotNone(grant)

        approved = self.registry.execute_tool(
            "write_file",
            args,
            turn_id=turn_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            enabled_tools=["write_file"],
            grant=grant,
            expected_approval_id=approval_id,
        )
        self.assertTrue(approved.success, approved.error)
        self.assertEqual(
            (control_dir / "policy.txt").read_text(encoding="utf-8"),
            "policy=true\n",
        )

    def test_run_command_rejects_cwd_escape(self):
        self.registry.policy.autonomy = AutonomyPolicy.preset("autonomous")

        res = self.registry.execute_tool(
            "run_command",
            {
                "argv": [sys.executable, "-c", "print('never')"],
                "cwd": "..",
            },
            enabled_tools=["run_command"],
        )

        self.assertFalse(res.success)
        self.assertIn("inside the workspace", res.error)

    def test_read_file_tool(self):
        f = self.root_path / "sample.py"
        f.write_text("line1\nline2\nline3\n", encoding='utf-8')

        res = self.registry.execute_tool("read_file", {"path": "sample.py", "start_line": 1, "end_line": 2}, enabled_tools=["read_file"])
        self.assertTrue(res.success)
        self.assertEqual(res.output, "line1\nline2")
        self.assertEqual(res.metadata["hash_scope"], "returned_range")
        self.assertEqual(res.metadata["end_line"], 2)

    def test_read_file_around_symbol_uses_repository_index(self):
        f = self.root_path / "sample.py"
        f.write_text("line1\n\ndef target_symbol():\n    return 1\n\nline6\n", encoding="utf-8")
        index = RepositoryIndex(self.tmp_dir.name, in_memory=True)
        index.build_or_update()
        registry = ToolRegistry(root_dir=self.tmp_dir.name, context_engine=ContextEngine(index))

        res = registry.execute_tool(
            "read_file",
            {"around_symbol": "target_symbol", "context_lines": 0},
            enabled_tools=["read_file"],
        )

        self.assertTrue(res.success, res.error)
        self.assertEqual(res.metadata["path"], "sample.py")
        self.assertIn("def target_symbol", res.output)
        index.close()

    def test_read_file_respects_max_bytes(self):
        f = self.root_path / "sample.py"
        f.write_text("abcdef\nuvwxyz\n", encoding="utf-8")

        res = self.registry.execute_tool(
            "read_file",
            {"path": "sample.py", "start_line": 1, "end_line": 2, "max_bytes": 5},
            enabled_tools=["read_file"],
        )

        self.assertTrue(res.success)
        self.assertTrue(res.truncated)
        self.assertLessEqual(len(res.output.encode("utf-8")), 5)

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

    def test_repository_map_uses_index_modes(self):
        (self.root_path / "controller.py").write_text(
            "from service import Service\nclass Controller:\n    def run(self):\n        return Service()\n",
            encoding="utf-8",
        )
        (self.root_path / "service.py").write_text("class Service:\n    pass\n", encoding="utf-8")
        index = RepositoryIndex(self.tmp_dir.name, in_memory=True)
        registry = ToolRegistry(root_dir=self.tmp_dir.name, context_engine=ContextEngine(index))

        workspace = registry.execute_tool("repository_map", {"mode": "workspace"}, enabled_tools=["repository_map"])
        symbols = registry.execute_tool("repository_map", {"mode": "symbol", "query": "Controller"}, enabled_tools=["repository_map"])
        impact = registry.execute_tool("repository_map", {"mode": "impact", "query": "Service"}, enabled_tools=["repository_map"])

        self.assertTrue(workspace.success, workspace.error)
        self.assertEqual(workspace.metadata["method"], "index")
        self.assertIn("files=2", workspace.output)
        self.assertIn("controller.py", symbols.output)
        self.assertIn("Controller", symbols.output)
        self.assertIn("controller.py -> service.py", impact.output)
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
