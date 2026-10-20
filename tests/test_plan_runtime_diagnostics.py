from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace

from kitt.core.runtime_config import RuntimeConfig
from kitt.core.turn_processor import TurnProcessor
from kitt.runtime.plan_runtime import execute_plan_action
from kitt.security.execution_sandbox import SandboxUnavailable
from kitt.tools.handlers.system import RunCommandHandler
from kitt.tools.registry import ToolRegistry


class PlanRuntimeContractRegressionTests(unittest.TestCase):
    def test_missing_proposal_has_actionable_error(self):
        plans = SimpleNamespace(submit=lambda *args: self.fail("invalid plan submitted"))
        runtime = SimpleNamespace(registry=SimpleNamespace(task_plans=plans), conversation_id="conv")
        with self.assertRaisesRegex(ValueError, "arguments.proposal") as error:
            execute_plan_action(
                runtime, "plan.submit",
                {"tasks": [{"id": "t01", "task": "One task", "validation": "Verify"}]},
                "turn", "MODEL", SimpleNamespace(), None, None,
            )
        self.assertIn("Do not pass tasks directly", str(error.exception))

    def test_valid_proposal_is_forwarded_without_rewriting_contract(self):
        original = {"schema_version": 1, "objective": "Test", "tasks": []}
        receipts = []

        def submit(conversation, turn, proposal, context):
            receipts.append((conversation, turn, proposal, context))
            return {"revision": 1}

        plans = SimpleNamespace(submit=submit)
        runtime = SimpleNamespace(registry=SimpleNamespace(task_plans=plans), conversation_id="conv")
        ctx = SimpleNamespace()
        result = execute_plan_action(
            runtime, "plan.submit", {"proposal": original}, "turn", "MODEL", ctx, None, None,
        )
        self.assertTrue(result.success)
        self.assertIs(receipts[0][2], original)
        self.assertIs(receipts[0][3], ctx)

    def test_verify_without_plan_gives_submission_guidance(self):
        plans = SimpleNamespace(inspect=lambda *_args: None)
        runtime = SimpleNamespace(registry=SimpleNamespace(task_plans=plans), conversation_id="conv")
        with self.assertRaisesRegex(ValueError, "requires an existing plan"):
            execute_plan_action(
                runtime, "plan.verify", {"task_id": "t01"}, "turn", "MODEL",
                SimpleNamespace(), None, None,
            )

    def test_prompt_uses_host_owned_proposal_schema(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            registry = ToolRegistry(root_dir=tmp)
            processor = TurnProcessor(
                root_dir=tmp, registry=registry,
                config=RuntimeConfig(history_enabled=False, persistence_enabled=False),
            )
            try:
                for agent_envelope in (False, True):
                    with self.subTest(agent_envelope=agent_envelope):
                        prompt = processor._tool_instructions(
                            ["kitt_runtime"], planned_tools=["kitt_runtime"],
                            agent_envelope=agent_envelope,
                        )
                        self.assertIn("plan.submit", prompt)
                        self.assertIn("proposal", prompt)
                        self.assertIn("schema_version", prompt)
                        self.assertIn("check_ids", prompt)
                        self.assertIn("Do not", prompt)
            finally:
                processor.close()
                registry.close()

    def test_sandbox_error_explains_host_remediation_without_reapproval(self):
        def fail_sandbox(*_args, **_kwargs):
            raise SandboxUnavailable("Strong sandbox profile 'workspace-write' requires functional bubblewrap >= 0.12")

        ctx = SimpleNamespace(
            security_context=None,
            approval_grant=None,
            origin="MODEL",
            registry=SimpleNamespace(process_runner=SimpleNamespace(run=fail_sandbox)),
        )
        result = RunCommandHandler().execute(
            {"argv": ["python3", "--version"], "network": False}, ctx,
        )
        self.assertFalse(result.success)
        self.assertIn("bubblewrap >= 0.12", result.error)
        self.assertIn("user-namespace support", result.error)
        self.assertIn("Do not retry", result.error)


if __name__ == "__main__":
    unittest.main()
