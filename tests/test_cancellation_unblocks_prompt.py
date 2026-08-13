import asyncio
import tempfile
import unittest
from kitt.core.runtime import KittRuntime
from kitt.ui.event_bridge import TurnEventBridge
from kitt.ui.reducer import reduce_ui_event
from kitt.ui.state import UIState, Toast

class TestCancellationUnblocksPrompt(unittest.TestCase):
    def test_cancellation_resets_is_thinking_clears_tasks_and_deactivates_bridge(self):
        async def run_test():
            with tempfile.TemporaryDirectory() as tmp_dir:
                state = UIState()
                runtime = KittRuntime.build(root_dir=tmp_dir)
                bridge = TurnEventBridge(runtime=runtime, on_event=lambda e: reduce_ui_event(state, e), invalidate=lambda: None)

                # 1. Simulate starting turn
                state.is_thinking = True
                state.init_turn_tasks("Tarefa demorada")
                bridge._active_turn_id = "turn_123"

                # 2. Trigger cancellation
                await bridge.cancel(reason="User cancelled")

                # 3. Check that bridge is NOT active, state.is_thinking is False, and active_tasks is empty
                self.assertFalse(bridge.is_active, "Bridge must NOT be active after cancel()")
                self.assertFalse(state.is_thinking, "state.is_thinking must be False after cancel()")
                self.assertEqual(len(state.active_tasks), 0, "active_tasks must be cleared after cancel()")
                self.assertIn("CANCELLED", state.status_text)

        asyncio.run(run_test())

if __name__ == "__main__":
    unittest.main()
