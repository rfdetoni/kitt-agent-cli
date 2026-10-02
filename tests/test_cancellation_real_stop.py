import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import TurnCancelled, TurnCompleted, TurnStarted
from kitt.core.turn_processor import TurnProcessor

async def _collect_async(stream):
    return [event async for event in stream]


class TestCancellationRealStop(unittest.TestCase):
    def test_turn_cancellation_aborts_processing_immediately(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            processor = TurnProcessor(root_dir=tmp_dir)
            cmd = TurnCommand(conversation_id="conv_1", prompt="Long running task", turn_id="turn_abc")

            # 1. Trigger cancellation
            cancel_events = list(processor.cancel_turn("turn_abc", reason="User pressed Ctrl+C"))

            self.assertIn("turn_abc", processor.cancelled_turns)
            self.assertEqual(len(cancel_events), 1)
            self.setIsInstance(cancel_events[0], TurnCancelled)
            self.assertEqual(cancel_events[0].reason, "User pressed Ctrl+C")

            # 2. Executing run_turn for a cancelled turn should abort immediately
            run_events = list(processor.run_turn(cmd))
            # Start event is emitted, then loop checks cancelled_turns and exits immediately
            self.assertNotIn("turn_abc", processor.cancelled_turns, "Cancelled turn should be cleaned up after turn aborts")
            self.assertFalse(any(isinstance(e, TurnCompleted) for e in run_events), "TurnCompleted must NOT be emitted after cancellation")


    def test_ctrl_c_does_not_block_next_prompt(self):
        async def scenario():
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
                processor = TurnProcessor(root_dir=tmp_dir)
                blocked = threading.Event()
                release_old = threading.Event()

                def fake_run_turn(cmd):
                    if cmd.turn_id == "turn_old":
                        blocked.set()
                    yield TurnStarted(
                        turn_id=cmd.turn_id,
                        conversation_id=cmd.conversation_id,
                        prompt=cmd.prompt,
                    )
                    if cmd.turn_id == "turn_old":
                        release_old.wait(timeout=5)
                        yield TurnCompleted(response="old")
                    else:
                        yield TurnCompleted(response="new")

                processor.run_turn = fake_run_turn
                old_stream = processor.arun_turn(
                    TurnCommand(
                        conversation_id="conv_1",
                        prompt="old",
                        turn_id="turn_old",
                    )
                )
                first = await anext(old_stream)
                self.assertIsInstance(first, TurnStarted)
                self.assertTrue(blocked.wait(timeout=1))

                list(
                    processor.cancel_turn(
                        "turn_old",
                        reason="User pressed Ctrl+C",
                        conversation_id="conv_1",
                    )
                )
                closing_old = asyncio.create_task(old_stream.aclose())
                await asyncio.sleep(0)

                new_events = await asyncio.wait_for(
                    _collect_async(
                        processor.arun_turn(
                            TurnCommand(
                                conversation_id="conv_1",
                                prompt="new",
                                turn_id="turn_new",
                            )
                        )
                    ),
                    timeout=1,
                )
                self.assertTrue(
                    any(
                        isinstance(event, TurnCompleted)
                        and event.response == "new"
                        for event in new_events
                    )
                )

                release_old.set()
                await asyncio.wait_for(closing_old, timeout=3)

        asyncio.run(scenario())

    def setIsInstance(self, obj, cls):
        self.assertTrue(isinstance(obj, cls), f"Expected instance of {cls}, got {type(obj)}")

if __name__ == "__main__":
    unittest.main()
