"""Regression: autonomous Wine workspace Git -C should use safe process cwd."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kitt.core.autonomy_policy import AutonomyPolicy
from kitt.runtime.safe_runtime import SafeRuntime
from kitt.security.capabilities import CAP_PROCESS_RUN
from kitt.security.context import ExecutionSecurityContext
from kitt.tools.registry import ToolRegistry, ToolResult
from kitt.tools.git_cwd_normalization import normalize_git_cwd_args


class TestGitCwdNormalization(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = ToolRegistry(root_dir=self.tmp.name)
        self.addCleanup(self.registry.close)
        self.registry.policy.autonomy = AutonomyPolicy.preset("autonomous")
        Path(self.tmp.name, "mbs-wine").mkdir()
        self.runtime = SafeRuntime(
            workspace_root=Path(self.tmp.name),
            workspace_id="ws-git-cwd",
            conversation_id="conv-git-cwd",
            tool_registry=self.registry,
        )
        self.context = ExecutionSecurityContext.create_user_context(
            workspace_id=self.runtime.workspace_id,
            conversation_id=self.runtime.conversation_id,
            capabilities={CAP_PROCESS_RUN},
        )

    def test_logged_git_status_normalizes_before_policy_and_execution(self):
        args = {
            "argv": ["git", "-C", "mbs-wine", "status", "--short", "--branch"],
            "timeout_seconds": 15,
        }
        normalized = normalize_git_cwd_args(args, self.registry.process_runner)
        self.assertEqual(normalized["argv"], ["git", "status", "--short", "--branch"])
        self.assertEqual(normalized["cwd"], "mbs-wine")
        self.assertNotIn("cwd", args)
        self.assertEqual(self.registry.policy.evaluate_argv(args["argv"]), "DENY")
        with patch.object(
            self.registry._handlers["run_command"], "execute",
            return_value=ToolResult(True, "git status stubbed"),
        ) as handler:
            result = self.runtime.execute(
                "process.run", args, security_context=self.context,
            )
        self.assertTrue(result.success, result.error)
        handler.assert_called_once()
        self.assertEqual(handler.call_args.args[0]["argv"], normalized["argv"])
        self.assertEqual(handler.call_args.args[0]["cwd"], "mbs-wine")

    def test_direct_registry_path_uses_same_safe_normalization(self):
        with patch.object(
            self.registry._handlers["run_command"], "execute",
            return_value=ToolResult(True, "ok"),
        ) as handler:
            result = self.registry.execute_tool(
                "run_command",
                {"argv": ["git", "-C", "mbs-wine", "status", "--short"]},
                security_context=self.context,
                workspace_id=self.runtime.workspace_id,
                conversation_id=self.runtime.conversation_id,
            )
        self.assertTrue(result.success, result.error)
        handler.assert_called_once()
        self.assertEqual(handler.call_args.args[0]["cwd"], "mbs-wine")

    def test_dangerous_git_directory_flags_stay_denied(self):
        unsafe = [
            ["git", "-C", "../outside", "status"],
            ["git", "-C", "/tmp", "status"],
            ["git", "-C", "mbs-wine", "status", "--git-dir=/tmp"],
            ["git", "-C", "mbs-wine", "status", "--work-tree=/tmp"],
            ["git", "-C", ".git", "status"],
            ["git", "-C", "mbs-wine", "-C", ".", "status"],
            ["git", "-C", "mbs-wine", "reset", "--hard"],
        ]
        with patch.object(
            self.registry._handlers["run_command"], "execute",
            return_value=ToolResult(True, "must not run"),
        ) as handler:
            for argv in unsafe:
                with self.subTest(argv=argv):
                    result = self.runtime.execute(
                        "process.run", {"argv": argv},
                        security_context=self.context,
                    )
                    self.assertFalse(result.success)
                    self.assertFalse(result.requires_approval)
            handler.assert_not_called()

    def test_explicit_cwd_combination_and_symlink_escape_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_git_cwd_args(
                {"argv": ["git", "-C", "mbs-wine", "status"], "cwd": "."},
                self.registry.process_runner,
            )
        outside = self.tmp.name + "-outside"
        with tempfile.TemporaryDirectory(dir=str(Path(self.tmp.name).parent)) as other:
            (Path(self.tmp.name) / "escape").symlink_to(other, target_is_directory=True)
            with self.assertRaises((PermissionError, ValueError)):
                normalize_git_cwd_args(
                    {"argv": ["git", "-C", "escape", "status"]},
                    self.registry.process_runner,
                )


if __name__ == "__main__":
    unittest.main()
