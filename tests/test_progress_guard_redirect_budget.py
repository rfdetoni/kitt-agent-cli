import unittest

from kitt.core import completion_guard as base_guard
from kitt.core.progress_guard import _ProgressAwareExecutionLedger
from kitt.core.turn_events import ToolCompleted, ToolStarted


class ProgressGuardRedirectBudgetTests(unittest.TestCase):
    @staticmethod
    def _exploration(call_id: str, path: str) -> ToolStarted:
        return ToolStarted(
            tool_name="kitt_runtime",
            args={"operation": "repo.read", "arguments": {"path": path}},
            call_id=call_id,
        )

    @staticmethod
    def _complete(call_id: str, path: str) -> ToolCompleted:
        return ToolCompleted(
            tool_name="kitt_runtime",
            success=True,
            output=f"read:{path}",
            call_id=call_id,
        )

    def test_redirect_renews_aggregate_exploration_window(self):
        ledger = _ProgressAwareExecutionLedger()

        for index in range(base_guard._MAX_EXPLORATIONS_WITHOUT_PROGRESS):
            call_id = f"read-{index}"
            path = f"src/file_{index}.py"
            self.assertIsNone(ledger.start(self._exploration(call_id, path)))
            ledger.complete(self._complete(call_id, path))

        blocked = self._exploration("blocked", "src/next.py")
        self.assertIn("too many exploration calls", ledger.start(blocked) or "")

        ledger.renew_exploration_budget()

        self.assertIsNone(ledger.start(blocked))

    def test_redirect_keeps_identical_exploration_history(self):
        ledger = _ProgressAwareExecutionLedger()
        path = "src/same.py"

        for index in range(base_guard._MAX_IDENTICAL_EXPLORATIONS_WITHOUT_PROGRESS - 1):
            call_id = f"same-{index}"
            self.assertIsNone(ledger.start(self._exploration(call_id, path)))
            ledger.complete(self._complete(call_id, path))

        repeated = self._exploration("same-blocked", path)
        self.assertIn("identical exploration repeated", ledger.start(repeated) or "")

        ledger.renew_exploration_budget()

        self.assertIn("identical exploration repeated", ledger.start(repeated) or "")


if __name__ == "__main__":
    unittest.main()
