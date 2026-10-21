"""Regressions for the autonomous Wine source checkout approval loop."""
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


class TestAutonomousGitFetch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = ToolRegistry(root_dir=self.temp.name)
        self.addCleanup(self.registry.close)
        self.registry.policy.autonomy = AutonomyPolicy.preset("allow_all")
        Path(self.temp.name, "mbs-wine").mkdir()
        self.runtime = SafeRuntime(
            workspace_root=Path(self.temp.name),
            workspace_id="ws-git-fetch",
            conversation_id="conv-git-fetch",
            tool_registry=self.registry,
        )
        self.root = ExecutionSecurityContext.create_user_context(
            workspace_id=self.runtime.workspace_id,
            conversation_id=self.runtime.conversation_id,
            capabilities={CAP_PROCESS_RUN},
        )

    def execute(self, argv, *, context=None, network=True, operation="process.run", origin="MODEL"):
        arguments = {
            "argv": argv, "cwd": "mbs-wine", "network": network,
            "timeout_seconds": 180,
        }
        with patch.object(
            self.registry.process_runner.sandbox, "is_strong_available",
            return_value=True,
        ), patch.object(
            self.registry._handlers["run_command"], "execute",
            return_value=ToolResult(True, "git checkout stubbed"),
        ) as handler:
            result = self.runtime.execute(
                operation, arguments, turn_id="fetch-test", origin=origin,
                security_context=self.root if context is None else context,
            )
        return result, handler

    def test_wine_submodule_checkout_without_repeated_modal(self):
        for argv in (
            ["git", "submodule", "add", "https://gitlab.winehq.org/wine/wine.git", "wine"],
            ["git", "clone", "https://github.com/wine-staging/wine-staging.git", "wine-staging"],
        ):
            with self.subTest(argv=argv):
                result, handler = self.execute(argv)
                self.assertTrue(result.success, result.error)
                self.assertFalse(result.requires_approval)
                handler.assert_called_once()

    def test_network_elevation_still_requires_approval_outside_narrow_git_rule(self):
        commands = (
            ["python3", "-c", "print('not an approved network fetch')"],
            ["git", "clone", "https://internal.example/private/repo.git", "private"],
            ["git", "clone", "https://github.com/user/repo.git?token=secret", "secret"],
            ["git", "clone", "https://github.com/user/repo.git", "../outside"],
            ["git", "clone", "https://github.com/user/repo.git", "/tmp/elsewhere"],
            ["git", "clone", "https://github.com/user/repo.git", "--upload-pack=bad"],
        )
        for argv in commands:
            with self.subTest(argv=argv):
                result, handler = self.execute(argv)
                self.assertFalse(result.success)
                self.assertTrue(result.requires_approval)
                handler.assert_not_called()

    def test_supervised_or_user_origin_never_grants_automatic_git_network(self):
        argv = ["git", "submodule", "add", "https://gitlab.winehq.org/wine/wine.git", "wine"]
        self.registry.policy.autonomy = AutonomyPolicy.preset("ask")
        result, handler = self.execute(argv)
        self.assertTrue(result.requires_approval)
        handler.assert_not_called()
        self.registry.policy.autonomy = AutonomyPolicy.preset("autonomous")
        result, handler = self.execute(argv, origin="USER")
        self.assertTrue(result.requires_approval)
        handler.assert_not_called()

    def test_scoped_principal_and_missing_strong_sandbox_do_not_get_network(self):
        argv = ["git", "clone", "https://github.com/wine-staging/wine-staging.git", "wine-staging"]
        scoped = self.root.derive_skill_context(
            "third-party", {CAP_PROCESS_RUN}, allowed_paths=["mbs-wine"]
        )
        result, handler = self.execute(argv, context=scoped)
        self.assertTrue(result.requires_approval)
        handler.assert_not_called()
        with patch.object(
            self.registry.process_runner.sandbox, "is_strong_available",
            return_value=False,
        ), patch.object(
            self.registry._handlers["run_command"], "execute",
            return_value=ToolResult(True, "must not run"),
        ) as handler:
            result = self.runtime.execute(
                "process.run",
                {"argv": argv, "cwd": "mbs-wine", "network": True},
                origin="MODEL", security_context=self.root,
            )
        self.assertTrue(result.requires_approval)
        handler.assert_not_called()


if __name__ == "__main__":
    unittest.main()
