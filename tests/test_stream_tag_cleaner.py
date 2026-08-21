import unittest
from kitt.core.turn_processor import TurnProcessor
from kitt.core.turn_events import TextDelta, ThinkingCompleted, ToolCallProposed

class FakeStreamClient:
    def __init__(self, chunks):
        self.chunks = chunks
    def chat_stream(self, *args, **kwargs):
        for c in self.chunks:
            yield c

class TestStreamTagCleaner(unittest.TestCase):
    def test_chunked_think_and_tool_tags_do_not_leak_to_text_delta(self):
        chunks = [
            "\n", "<", "think>", "Planejando codigo...", "</think>",
            "\n", "<", "kitt-tool>", '{"name": "write_file", "arguments": {"path": "main.py"}}', "</kitt-tool>"
        ]
        client = FakeStreamClient(chunks)
        processor = TurnProcessor.__new__(TurnProcessor)
        processor.cancelled_turns = set()

        events = []
        for full_res, ev in processor._stream_execution_response(client, [], ""):
            if ev is not None:
                events.append(ev)

        deltas = [ev.delta for ev in events if isinstance(ev, TextDelta)]
        combined_deltas = "".join(deltas)

        self.assertNotIn("<think>", combined_deltas)
        self.assertNotIn("</think>", combined_deltas)
        self.assertNotIn("<kitt-tool", combined_deltas)
        self.assertNotIn("</kitt-tool>", combined_deltas)
        self.assertNotIn("Planejando codigo", combined_deltas)

        thought_events = [ev for ev in events if isinstance(ev, ThinkingCompleted)]
        self.assertTrue(len(thought_events) >= 1)
        self.assertGreaterEqual(thought_events[0].tokens, 1)

    def test_normal_text_with_think_block(self):
        chunks = ["<think>Pensando...</think>", "Aqui ", "está ", "a resposta."]
        client = FakeStreamClient(chunks)
        processor = TurnProcessor.__new__(TurnProcessor)
        processor.cancelled_turns = set()

        events = []
        for full_res, ev in processor._stream_execution_response(client, [], ""):
            if ev is not None:
                events.append(ev)

        deltas = [ev.delta for ev in events if isinstance(ev, TextDelta)]
        combined_deltas = "".join(deltas)

        self.assertNotIn("<think>", combined_deltas)
        self.assertNotIn("</think>", combined_deltas)
        self.assertEqual(combined_deltas, "Aqui está a resposta.")

if __name__ == "__main__":
    unittest.main()
