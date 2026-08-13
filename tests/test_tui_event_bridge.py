import asyncio
import unittest
from types import SimpleNamespace

from kitt.core.turn_events import TextDelta, TurnCompleted, TurnStarted
from kitt.ui.event_bridge import TurnEventBridge


class Processor:
    def run_turn(self, command):
        yield TurnStarted(turn_id=command.turn_id, conversation_id=command.conversation_id, prompt=command.prompt)
        for _ in range(1000):
            yield TextDelta(delta="x")
        yield TurnCompleted(response="x" * 1000)

    def cancel_turn(self, turn_id, reason):
        return iter(())


class TestTurnEventBridge(unittest.IsolatedAsyncioTestCase):
    async def test_coalesces_deltas_and_keeps_final_text(self):
        events, invalidations = [], []
        runtime = SimpleNamespace(processor=Processor(), history=SimpleNamespace(repo=SimpleNamespace(save_message=lambda *a: None)))
        bridge = TurnEventBridge(runtime, events.append, lambda: invalidations.append(1), max_queue=16)
        await bridge.start("hello", "conversation", no_history=True)
        await asyncio.wait_for(bridge._consumer, 2)
        text = "".join(event.delta for event in events if isinstance(event, TextDelta))
        self.assertEqual(len(text), 1000)
        self.assertLess(len(invalidations), 1000)
        self.assertLessEqual(bridge._queue.maxsize, 128)
        await bridge.shutdown()


if __name__ == "__main__":
    unittest.main()
