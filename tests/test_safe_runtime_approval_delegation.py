import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kitt.core.autonomy_policy import AutonomyPolicy
from kitt.runtime.safe_runtime import SafeRuntime
from kitt.security.capabilities import CAP_PROCESS_RUN
from kitt.security.context import ExecutionSecurityContext
from kitt.tools.registry import ToolRegistry


class TestSafeRuntimeApprovalDelegation(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root_path = Path(self.tmp_dir.name).resolve()
        self.registry = ToolRegistry(root_dir=self.tmp_dir.name)
        self.registry.policy.autonomy = AutonomyPolicy.preset("autonomous")
        self.runtime = SafeRuntime(
            workspace_root=self.root_path,
            workspace_id="ws-runtime-approval",
            conversation_id="conv-runtime-approval",
            tool_registry=self.registry,
        )

    def tearDown(self):
        self.registry.close()
        self.tmp_dir.cleanup()

    def _request_and_issue_grant(self, args, approval_id):
        turn_id = "turn-runtime-approval"
        action_hash = self.registry.policy.generate_action_hash("run_command", args)
        self.registry.approval_manager.register_request(
            turn_id,
            self.runtime.conversation_id,
            self.runtime.workspace_id,
            action_hash,
            approval_id,
            tool_name="run_command",
        )
        grant = self.registry.approval_manager.issue_grant(
            turn_id,
            self.runtime.conversation_id,
            self.runtime.workspace_id,
            action_hash,
            approval_id=approval_id,
        )
        self.assertIsNotNone(grant)
        return turn_id, grant

    def test_process_run_delegates_exact_approved_grant_to_run_command(self):
        args = {
            "argv": [sys.executable, "-c", "print('delegated-approved')"],
        }
        turn_id = "turn-runtime-approval"

        with patch.object(
            self.registry.process_runner.sandbox,
            "is_strong_available",
            return_value=False,
        ):
            pending = self.runtime.execute(
                "process.run",
                args,
                turn_id=turn_id,
                origin="MODEL",
                effective_capabilities={CAP_PROCESS_RUN},
            )

        self.assertFalse(pending.success)
        self.assertTrue(pending.requires_approval)
        self.assertEqual(pending.resume_tool_name, "run_command")

        _, grant = self._request_and_issue_grant(args, "approval-runtime-exact")

        with patch.object(
            self.registry.process_runner.sandbox,
            "is_strong_available",
            return_value=False,
        ):
            approved = self.runtime.execute(
                "process.run",
                args,
                turn_id=turn_id,
                origin="MODEL",
                effective_capabilities={CAP_PROCESS_RUN},
                approval_grant=grant,
                expected_approval_id="approval-runtime-exact",
            )

        self.assertTrue(approved.success, approved.error)
        self.assertEqual(str(approved.data).strip(), "delegated-approved")

    def test_registry_wrapper_defers_effective_grant_consumption_to_nested_tool(self):
        args = {
            "argv": [sys.executable, "-c", "print('wrapper-approved')"],
        }
        wrapper_args = {
            "operation": "process.run",
            "arguments": args,
        }
        turn_id = "turn-wrapper-approval"
        approval_id = "approval-wrapper-effective"
        security_context = ExecutionSecurityContext.create_user_context(
            workspace_id=self.runtime.workspace_id,
            conversation_id=self.runtime.conversation_id,
            turn_id=turn_id,
            capabilities={CAP_PROCESS_RUN},
        )

        with patch.object(
            self.registry.process_runner.sandbox,
            "is_strong_available",
            return_value=False,
        ):
            pending = self.registry.execute_tool(
                "kitt_runtime",
                wrapper_args,
                turn_id=turn_id,
                conversation_id=self.runtime.conversation_id,
                workspace_id=self.runtime.workspace_id,
                security_context=security_context,
            )

        self.assertFalse(pending.success)
        self.assertTrue(pending.requires_approval)
        self.assertEqual(pending.metadata.get("approval_action"), "run_command")
        self.assertEqual(pending.metadata.get("approval_payload"), args)

        action_hash = self.registry.policy.generate_action_hash(
            "run_command", args
        )
        self.registry.approval_manager.register_request(
            turn_id,
            self.runtime.conversation_id,
            self.runtime.workspace_id,
            action_hash,
            approval_id,
            tool_name="run_command",
        )
        grant = self.registry.approval_manager.issue_grant(
            turn_id,
            self.runtime.conversation_id,
            self.runtime.workspace_id,
            action_hash,
            approval_id=approval_id,
        )
        self.assertIsNotNone(grant)
        self.assertFalse(self.registry.approval_manager.is_nonce_used(grant.nonce))

        with patch.object(
            self.registry.process_runner.sandbox,
            "is_strong_available",
            return_value=False,
        ):
            approved = self.registry.execute_tool(
                "kitt_runtime",
                wrapper_args,
                turn_id=turn_id,
                conversation_id=self.runtime.conversation_id,
                workspace_id=self.runtime.workspace_id,
                grant=grant,
                expected_approval_id=approval_id,
                security_context=security_context,
            )

        self.assertTrue(approved.success, approved.error)
        self.assertIn("wrapper-approved", approved.output)
        self.assertTrue(self.registry.approval_manager.is_nonce_used(grant.nonce))

    def test_process_run_rejects_mismatched_delegated_grant(self):
        marker = self.root_path / "must-not-exist.txt"
        requested_args = {
            "argv": [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('must-not-exist.txt').write_text('bad')",
            ],
        }
        different_args = {
            "argv": [sys.executable, "-c", "print('different-action')"],
        }
        turn_id, grant = self._request_and_issue_grant(
            different_args,
            "approval-runtime-mismatch",
        )

        with patch.object(
            self.registry.process_runner.sandbox,
            "is_strong_available",
            return_value=False,
        ):
            rejected = self.runtime.execute(
                "process.run",
                requested_args,
                turn_id=turn_id,
                origin="MODEL",
                effective_capabilities={CAP_PROCESS_RUN},
                approval_grant=grant,
                expected_approval_id="approval-runtime-mismatch",
            )

        self.assertFalse(rejected.success)
        self.assertTrue(rejected.requires_approval)
        self.assertIn("invalid, expired, mismatched", rejected.error)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
