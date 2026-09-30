import sys
import time
import unittest
from types import SimpleNamespace
from tempfile import TemporaryDirectory

from kitt.core.autonomy_policy import AutonomyPolicy
from kitt.runtime.process_lifecycle import ManagedProcessManager
from kitt.security.capabilities import CAP_PROCESS_RUN
from kitt.security.context import ExecutionSecurityContext
from kitt.tools.approval import ApprovalManager
from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.process_runner import ProcessRunner


class _Ledger:
    def __init__(self):
        self.events = []

    def append_event(
        self,
        conversation_id,
        event_type,
        payload,
        *,
        turn_id=None,
        **_kwargs,
    ):
        self.events.append(
            {
                "conversation_id": conversation_id,
                "turn_id": turn_id,
                "event_type": event_type,
                "payload": dict(payload),
            }
        )


class ManagedProcessLifecycleTests(unittest.TestCase):
    def _manager(self, root):
        approval = ApprovalManager(workspace_id="ws")
        policy = PolicyEngine(
            root,
            autonomy=AutonomyPolicy.preset("autonomous"),
            approval_manager=approval,
        )
        registry = SimpleNamespace(
            policy=policy,
            approval_manager=approval,
        )
        ledger = _Ledger()
        manager = ManagedProcessManager(
            ProcessRunner(root),
            registry,
            workspace_id="ws",
            ledger=ledger,
        )
        context = ExecutionSecurityContext.create_user_context(
            "ws",
            "conv",
            "turn-1",
            capabilities=[CAP_PROCESS_RUN],
        )
        return manager, registry, ledger, context

    def test_output_and_exit_are_durable_observations(self):
        with TemporaryDirectory() as root:
            manager, _registry, ledger, context = self._manager(root)
            try:
                started = manager.start(
                    argv=[
                        sys.executable,
                        "-u",
                        "-c",
                        "print('hello-managed-process')",
                    ],
                    conversation_id="conv",
                    turn_id="turn-1",
                    security_context=context,
                    sandbox_profile="full-access",
                )
                deadline = time.time() + 5
                result = None
                while time.time() < deadline:
                    result = manager.read(
                        started["process_id"],
                        security_context=context,
                    )
                    if result["returncode"] is not None:
                        break
                    time.sleep(0.02)

                self.assertIsNotNone(result)
                self.assertEqual(result["returncode"], 0)
                self.assertTrue(
                    any(
                        "hello-managed-process" in item["content"]
                        for item in result["events"]
                    )
                )
                event_types = [item["event_type"] for item in ledger.events]
                self.assertIn("PROCESS_STARTED", event_types)
                self.assertIn("PROCESS_OUTPUT", event_types)
                self.assertIn("PROCESS_EXIT", event_types)
            finally:
                manager.close()

    def test_control_rejects_stale_authority_snapshot(self):
        with TemporaryDirectory() as root:
            manager, registry, _ledger, context = self._manager(root)
            try:
                started = manager.start(
                    argv=[
                        sys.executable,
                        "-u",
                        "-c",
                        "import time; time.sleep(10)",
                    ],
                    conversation_id="conv",
                    turn_id="turn-1",
                    security_context=context,
                    sandbox_profile="full-access",
                )
                registry.policy.autonomy = AutonomyPolicy.preset("read_only")

                with self.assertRaises(PermissionError):
                    manager.stop(
                        started["process_id"],
                        security_context=context.with_turn("turn-2"),
                        turn_id="turn-2",
                    )
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
