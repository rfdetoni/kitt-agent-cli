from __future__ import annotations

import asyncio
import threading
import unittest
from unittest.mock import patch

from kitt.daemon.protocol import DaemonEvent
from kitt.remote.gateway import DaemonGateway


class FakeDaemonClient:
    def __init__(self, *args, **kwargs):
        self._connected = True

    async def connect(self):
        return True

    async def send_request(self, action, params=None, timeout=15.0):
        if action == "events_since":
            return {
                "status": "ok",
                "events": [
                    DaemonEvent(1, "s1", "TurnStarted", {"turn_id": "t1"}, 1.0).to_dict(),
                    DaemonEvent(2, "s1", "TextDelta", {"delta": "a"}, 2.0).to_dict(),
                ],
                "has_more": False,
                "next_sequence": 2,
            }
        return {"status": "ok"}

    async def attach(self, session_id, last_sequence=0, on_event=None, **kwargs):
        # Simulate a realtime event racing with attach replay, plus an overlap
        # in the attach page. The gateway must emit 1,2,3 exactly once.
        if on_event:
            on_event(DaemonEvent(3, session_id, "TextDelta", {"delta": "b"}, 3.0))
        return {
            "status": "ok",
            "events": [DaemonEvent(2, session_id, "TextDelta", {"delta": "a"}, 2.0)],
        }

    async def close(self):
        self._connected = False


class GatewayReplayTests(unittest.TestCase):
    def test_replay_attach_race_is_ordered_and_deduplicated(self):
        gateway = DaemonGateway(".")
        stop = threading.Event()
        emitted = []

        def emit(evt):
            emitted.append(evt.sequence_id)
            if evt.sequence_id == 3:
                stop.set()

        with patch("kitt.remote.gateway.DaemonClient", FakeDaemonClient):
            asyncio.run(
                gateway._stream_events_async(
                    session_id="s1",
                    last_sequence=0,
                    emit=emit,
                    heartbeat=lambda: None,
                    stop=stop,
                )
            )
        self.assertEqual(emitted, [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
