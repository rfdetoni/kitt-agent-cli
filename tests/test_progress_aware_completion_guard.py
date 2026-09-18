import unittest

from kitt.core.completion_guard import _ExecutionProgressLedger
from kitt.core.turn_events import ToolCompleted, ToolStarted


def _started(call_id: str):
    return ToolStarted(
        tool_name="kitt_runtime",
        args={"operation": "repo.list", "arguments": {"path": "."}},
        call_id=call_id,
    )


class ProgressAwareCompletionGuardTests(unittest.TestCase):
    def test_same_call_with_changed_result_is_legitimate_progress(self):
        ledger = _ExecutionProgressLedger()

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
        ledger = _ExecutionProgressLedger()

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
        ledger = _ExecutionProgressLedger()
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


if __name__ == "__main__":
    unittest.main()
