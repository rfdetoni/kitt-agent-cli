import tempfile
import unittest
from pathlib import Path
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import TurnCancelled, TurnCompleted, TurnStarted
from kitt.core.turn_processor import TurnProcessor

class TestCancellationRealStop(unittest.TestCase):
    def test_turn_cancellation_aborts_processing_immediately(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
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

    def setIsInstance(self, obj, cls):
        self.assertTrue(isinstance(obj, cls), f"Expected instance of {cls}, got {type(obj)}")

if __name__ == "__main__":
    unittest.main()
