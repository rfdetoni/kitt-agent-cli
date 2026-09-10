import asyncio
import threading
import time
import unittest
from unittest.mock import patch

from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import TurnCompleted, TurnStarted
from kitt.core.turn_processor import TurnProcessor


class _NaturalCompletionHarness:
    def __init__(self):
        self.cancelled = []

    def run_turn(self, cmd):
        yield TurnCompleted(response="done")

    def _mark_cancelled(self, turn_id):
        self.cancelled.append(turn_id)
        return False


class _AbandonedStreamHarness:
    def __init__(self):
        self.cancelled = []

    def run_turn(self, cmd):
        yield TurnStarted(turn_id=cmd.turn_id, conversation_id=cmd.conversation_id, prompt=cmd.prompt)
        time.sleep(0.05)
        yield TurnCompleted(response="late")

    def _mark_cancelled(self, turn_id):
        self.cancelled.append(turn_id)
        return False


class TestAsyncTurnApprovalLifecycle(unittest.TestCase):
    def test_natural_stream_completion_does_not_infer_cancellation_from_thread_liveness(self):
        async def run_test():
            harness = _NaturalCompletionHarness()
            cmd = TurnCommand(conversation_id="conv-approval", prompt="mkdir -p teste", turn_id="turn-approval")

            # Force the exact old failure condition: after the sentinel is received,
            # Thread.is_alive() still reports true for the producer scheduling tick.
            with patch.object(threading.Thread, "is_alive", return_value=True):
                events = [event async for event in TurnProcessor.arun_turn(harness, cmd)]

            self.assertTrue(any(isinstance(event, TurnCompleted) for event in events))
            self.assertEqual(harness.cancelled, [])

        asyncio.run(run_test())

    def test_abandoning_stream_before_completion_still_marks_turn_cancelled(self):
        async def run_test():
            harness = _AbandonedStreamHarness()
            cmd = TurnCommand(conversation_id="conv-cancel", prompt="long task", turn_id="turn-cancel")
            stream = TurnProcessor.arun_turn(harness, cmd)

            first = await anext(stream)
            self.assertIsInstance(first, TurnStarted)
            await stream.aclose()

            self.assertEqual(harness.cancelled, ["turn-cancel"])

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
