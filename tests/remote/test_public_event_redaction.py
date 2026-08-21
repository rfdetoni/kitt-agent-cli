import unittest

from kitt.daemon.redaction import sanitize_public_event_payload


class PublicEventRedactionTests(unittest.TestCase):
    def test_thinking_event_never_exposes_reasoning(self):
        payload = {
            "duration_ms": 123,
            "tokens": 42,
            "thought": "private reasoning",
            "reasoning_content": "also private",
            "nested": {"chain_of_thought": "private", "safe": "ok"},
        }
        clean = sanitize_public_event_payload("ThinkingCompleted", payload)
        self.assertEqual(clean["duration_ms"], 123)
        self.assertEqual(clean["tokens"], 42)
        self.assertNotIn("thought", clean)
        self.assertNotIn("reasoning_content", clean)
        self.assertEqual(clean["nested"], {"safe": "ok"})

    def test_unrelated_tool_payload_keeps_legitimate_reasoning_key(self):
        payload = {"reasoning": "domain field", "output": "ok"}
        self.assertEqual(sanitize_public_event_payload("ToolCompleted", payload), payload)

    def test_payload_is_bounded(self):
        clean = sanitize_public_event_payload(
            "ToolCompleted",
            {"text": "x" * (70 * 1024), "items": list(range(300))},
        )
        self.assertLess(len(clean["text"]), 66 * 1024)
        self.assertLessEqual(len(clean["items"]), 129)


if __name__ == "__main__":
    unittest.main()
