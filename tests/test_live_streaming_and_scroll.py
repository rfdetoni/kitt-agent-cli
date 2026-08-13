import asyncio
import tempfile
import unittest
from unittest.mock import MagicMock
from kitt.core.runtime import KittRuntime
from kitt.ui.app import KittUIApp
from kitt.core.turn_events import TurnStarted, TextDelta, TurnCompleted

class TestLiveStreamingAndScroll(unittest.TestCase):
    def test_on_event_triggers_invalidate(self):
        async def run_test():
            with tempfile.TemporaryDirectory() as tmp_dir:
                runtime = KittRuntime.build(root_dir=tmp_dir)
                app = KittUIApp(runtime=runtime)
                app.build_application()

                app.application.invalidate = MagicMock()

                # 1. Simulate TurnStarted event
                app._on_event(TurnStarted(turn_id="t1", conversation_id="c1", prompt="Olá KITT"))
                app.application.invalidate.assert_called()

                # 2. Simulate streaming TextDelta
                app.application.invalidate.reset_mock()
                app._on_event(TextDelta(delta="Olá! Como posso ajudar?"))
                app.application.invalidate.assert_called()

                # 3. Check transcript content
                self.assertIn("Olá! Como posso ajudar?", app.state.transcript[-1].text)

                # 4. Simulate TurnCompleted
                app._on_event(TurnCompleted(response="Olá! Como posso ajudar?"))
                self.assertFalse(app.state.is_thinking)

        asyncio.run(run_test())

if __name__ == "__main__":
    unittest.main()
