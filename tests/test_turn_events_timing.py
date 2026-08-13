import unittest
from unittest.mock import MagicMock
from kitt.core.turn_processor import TurnProcessor
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import ToolStarted, ToolCompleted, ThinkingStarted, ThinkingCompleted

class TestTurnEventsTiming(unittest.TestCase):
    def test_tool_call_ids_are_unique_and_pair_correctly(self):
        processor = TurnProcessor(root_dir=".")
        fake_client = MagicMock()

        # Responses for 2 tool calls then completion
        fake_client.chat_stream.side_effect = [
            iter(['<kitt-tool>{"name":"read_file","arguments":{"path":"a.py"}}</kitt-tool>']),
            iter(['<kitt-tool>{"name":"read_file","arguments":{"path":"b.py"}}</kitt-tool>']),
            iter(['Done!']),
        ]

        processor.execution_client = fake_client
        processor.context_client = fake_client

        cmd = TurnCommand(turn_id="turn-1", conversation_id="conv-1", prompt="read a.py and b.py")
        events = list(processor.run_turn(cmd))

        tool_started_events = [e for e in events if isinstance(e, ToolStarted)]
        tool_completed_events = [e for e in events if isinstance(e, ToolCompleted)]

        self.assertEqual(len(tool_started_events), 2)
        self.assertEqual(len(tool_completed_events), 2)

        start_ids = [e.call_id for e in tool_started_events]
        comp_ids = [e.call_id for e in tool_completed_events]

        self.assertEqual(len(set(start_ids)), 2, "call_id must be unique per tool call")
        self.assertEqual(start_ids[0], comp_ids[0])
        self.assertEqual(start_ids[1], comp_ids[1])

if __name__ == "__main__":
    unittest.main()
