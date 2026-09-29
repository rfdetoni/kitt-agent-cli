import unittest
from types import SimpleNamespace

from kitt.core.completion_guard import (
    _ProgressAwareExecutionLedger,
    build_completion_contract,
    requires_workspace_mutation,
)
from kitt.core.turn_events import ToolCompleted, ToolStarted


def _started(call_id: str):
    return ToolStarted(
        tool_name="kitt_runtime",
        args={"operation": "repo.list", "arguments": {"path": "."}},
        call_id=call_id,
    )


class ProgressAwareCompletionGuardTests(unittest.TestCase):
    def test_same_call_with_changed_result_is_legitimate_progress(self):
        ledger = _ProgressAwareExecutionLedger()

        for index, output in enumerate(("one", "two", "three"), start=1):
            call_id = f"list-{index}"
            self.assertIsNone(ledger.start(_started(call_id)))
            ledger.complete(
                ToolCompleted(
                    tool_name="kitt_runtime",
                    success=True,
                    output=output,
                    call_id=call_id,
                )
            )

    def test_third_identical_result_is_blocked_before_execution(self):
        ledger = _ProgressAwareExecutionLedger()

        for index in (1, 2):
            call_id = f"list-{index}"
            self.assertIsNone(ledger.start(_started(call_id)))
            ledger.complete(
                ToolCompleted(
                    tool_name="kitt_runtime",
                    success=True,
                    output='{"entries":["README.md"]}',
                    call_id=call_id,
                )
            )

        stall = ledger.start(_started("list-3"))
        self.assertIsNotNone(stall)
        self.assertIn("identical exploration repeated without progress", stall)
        self.assertIn("repo.list", stall)

    def test_deterministic_capability_failure_is_not_retried_unchanged(self):
        ledger = _ProgressAwareExecutionLedger()
        first = ToolStarted(
            tool_name="kitt_runtime",
            args={"operation": "repo.diagnostics", "arguments": {"path": "."}},
            call_id="diag-1",
        )
        self.assertIsNone(ledger.start(first))
        ledger.complete(
            ToolCompleted(
                tool_name="kitt_runtime",
                success=False,
                error="Unknown runtime operation: 'repo.diagnostics'",
                call_id="diag-1",
            )
        )

        retry = ToolStarted(
            tool_name="kitt_runtime",
            args={"operation": "repo.diagnostics", "arguments": {"path": "."}},
            call_id="diag-2",
        )
        stall = ledger.start(retry)
        self.assertIsNotNone(stall)
        self.assertIn("repo.diagnostics", stall)


    def test_validation_is_invalidated_by_later_mutation(self):
        ledger = _ProgressAwareExecutionLedger()
        build_args = {
            "operation": "process.run",
            "arguments": {"argv": ["npm", "run", "build"], "cwd": "faztudo"},
        }
        ledger.start(ToolStarted(tool_name="kitt_runtime", args=build_args, call_id="build-1"))
        ledger.complete(ToolCompleted(
            tool_name="kitt_runtime", success=True, output="ok", call_id="build-1"
        ))
        self.assertTrue(ledger.validation_succeeded)

        write_args = {
            "operation": "repo.write_file",
            "arguments": {"path": "faztudo/src/app/app.component.ts", "content": "changed"},
        }
        ledger.start(ToolStarted(tool_name="kitt_runtime", args=write_args, call_id="write-1"))
        ledger.complete(ToolCompleted(
            tool_name="kitt_runtime", success=True, output="written", call_id="write-1"
        ))
        self.assertFalse(ledger.validation_succeeded)
        self.assertEqual(ledger.validated_scopes, frozenset())

    def test_failed_validation_blocks_until_same_scope_passes(self):
        ledger = _ProgressAwareExecutionLedger()
        args = {
            "operation": "process.run",
            "arguments": {"argv": ["npm", "run", "build"], "cwd": "faztudo"},
        }
        ledger.start(ToolStarted(tool_name="kitt_runtime", args=args, call_id="build-fail"))
        ledger.complete(ToolCompleted(
            tool_name="kitt_runtime",
            success=False,
            output="TS2551: Property does not exist",
            error="Command exited with code 1",
            call_id="build-fail",
        ))
        self.assertFalse(ledger.validation_succeeded)
        self.assertIn("faztudo", ledger.failed_validation_scopes)

        ledger.start(ToolStarted(tool_name="kitt_runtime", args=args, call_id="build-pass"))
        ledger.complete(ToolCompleted(
            tool_name="kitt_runtime", success=True, output="ok", call_id="build-pass"
        ))
        self.assertTrue(ledger.validation_succeeded)
        self.assertNotIn("faztudo", ledger.failed_validation_scopes)

    def test_explicit_test_request_requires_real_validation(self):
        contract = build_completion_contract(
            "Teste o projeto Angular com npm run build e corrija os bugs."
        )
        self.assertTrue(contract.require_validation)

    def test_test_intent_with_explicit_fix_still_requires_mutation(self):
        processor = SimpleNamespace(
            session_state=SimpleNamespace(
                last_task=SimpleNamespace(
                    original_prompt="",
                    intent="TEST",
                    actions=["analyze", "edit"],
                )
            )
        )
        cmd = SimpleNamespace(
            mode="auto",
            prompt="Teste o projeto e corrija os bugs encontrados.",
        )
        self.assertTrue(requires_workspace_mutation(processor, cmd))


if __name__ == "__main__":
    unittest.main()
