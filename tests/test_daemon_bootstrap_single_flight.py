from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kitt.ui.event_bridge import TurnEventBridge


class DaemonBootstrapSingleFlightTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_bootstrap_attaches_and_configures_once(self):
        calls = []

        class FakeDaemon:
            def __init__(self, workspace_dir, event_sink):
                self.connected = False
                self.attached_session_id = None

            async def connect(self):
                calls.append("connect")
                await asyncio.sleep(0)
                self.connected = True
                return True

            async def set_logging(self, *_):
                calls.append("logging")

            async def attach(self, session_id):
                calls.append("attach")
                await asyncio.sleep(0.01)
                self.attached_session_id = session_id
                return True

            async def set_reasoning(self, *_):
                calls.append("reasoning")

            async def set_autonomy(self, *_):
                calls.append("autonomy")

            async def close(self):
                pass

        rt = SimpleNamespace(
            config=SimpleNamespace(daemon_enabled=True),
            canonical_root="/tmp/kitt-test-daemon-bootstrap",
            processor=SimpleNamespace(reasoning_effort=50),
            autonomy_store=SimpleNamespace(get=lambda: SimpleNamespace(level="allow-all")),
            events=None,
        )
        bridge = TurnEventBridge(rt, lambda event: None, lambda: None)
        try:
            with patch("kitt.ui.daemon_bridge.DaemonUIBridge", FakeDaemon):
                results = await asyncio.gather(
                    *(bridge._ensure_daemon("same-conversation") for _ in range(3))
                )
            self.assertEqual(results, [True, True, True])
            self.assertEqual(
                calls, ["connect", "logging", "attach", "reasoning", "autonomy"]
            )
        finally:
            bridge._executor.shutdown(wait=False, cancel_futures=True)

    async def test_simultaneous_submit_cannot_submit_same_turn_twice(self):
        submitted = []

        class FakeDaemon:
            attached_session_id = "same-conversation"

            async def submit_turn(self, prompt, **kwargs):
                submitted.append(prompt)
                await asyncio.sleep(0.01)
                return "turn-1"

        rt = SimpleNamespace(config=SimpleNamespace(daemon_enabled=True), events=None)
        bridge = TurnEventBridge(rt, lambda event: None, lambda: None)

        async def ensure(_conversation_id):
            bridge._daemon_bridge = FakeDaemon()
            await asyncio.sleep(0.01)
            return True

        try:
            with patch.object(bridge, "_ensure_daemon", side_effect=ensure):
                results = await asyncio.gather(
                    bridge.start("single-prompt", "same-conversation"),
                    bridge.start("single-prompt", "same-conversation"),
                    return_exceptions=True,
                )
            self.assertEqual(submitted, ["single-prompt"])
            self.assertEqual(results[0], "turn-1")
            self.assertIsInstance(results[1], RuntimeError)
        finally:
            bridge._executor.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    unittest.main()
