import time
import unittest
from types import SimpleNamespace

from kitt.core.turn_processor import TurnProcessor


class Harness:
    reasoning_effort = 50
    _clean_visible_text = staticmethod(TurnProcessor._clean_visible_text)
    _record_latency = TurnProcessor._record_latency

    def __init__(self):
        self.events = []

    def _cancel_requested(self, turn_id):
        return False

    def _emit(self, name, payload):
        self.events.append((name, payload))


class Client:
    profile = SimpleNamespace(model="chatgpt-web")

    def chat_stream(self, messages, **kwargs):
        yield "hello"


class TestLatencyTelemetry(unittest.TestCase):
    def test_callback_shape(self):
        h = Harness()
        payload = h._record_latency(
            "t1",
            "semantic",
            12.345,
            elapsed_ms=20,
            detail={"source": "local"},
        )
        self.assertEqual(payload["duration_ms"], 12.35)
        self.assertEqual(h.events[0][0], "LatencyRecorded")

    def test_ttft_is_side_channel(self):
        # Timing must be observable without changing the existing streaming tuple contract.
        h = Harness()
        result = list(
            TurnProcessor._stream_execution_response(
                h,
                Client(),
                [{"role": "user", "content": "x"}],
                "sys",
                turn_id="t2",
                started_at=time.time(),
                session_key="c1",
            )
        )
        self.assertTrue(
            any(
                name == "LatencyRecorded" and payload["phase"] == "model_ttft"
                for name, payload in h.events
            )
        )
        self.assertTrue(any(text == "hello" for text, _ in result))


if __name__ == "__main__":
    unittest.main()
