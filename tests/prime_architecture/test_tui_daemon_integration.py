import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from kitt.daemon.server import DaemonServer
from kitt.core.turn_events import TurnStarted, TurnCompleted
from kitt.history.database import HistoryDatabase
from kitt.ui.daemon_bridge import DaemonUIBridge, map_daemon_event_to_turn_event
from kitt.daemon.protocol import DaemonEvent


class FakeClient:
    def __init__(self, text: str):
        self.text = text

    def chat(self, *args, **kwargs):
        return self.text

    def chat_stream(self, *args, **kwargs):
        for chunk in (self.text[:4], self.text[4:]):
            yield chunk


class TestTUIDaemonIntegration(unittest.IsolatedAsyncioTestCase):
    """Integration tests for DaemonUIBridge and Terminal UI persistence contracts."""

    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

        self.server = DaemonServer(
            workspace_root=str(self.root),
            context_client=FakeClient('{"intent":"ASK","confidence":1.0}'),
            execution_client=FakeClient("Hello from TUI Daemon Bridge"),
        )
        await self.server.start()

        rt = await self.server._get_or_create_runtime()
        conv = rt.history.new_conversation("TUI Session")
        self.session_id = conv["id"]

    async def asyncTearDown(self):
        await self.server.stop()
        self.temp_dir.cleanup()

    def test_01_map_daemon_event_to_turn_event(self):
        """Verify DaemonEvent mapping produces typed TurnEvents."""
        evt_start = DaemonEvent(
            sequence_id=1,
            session_id="s1",
            event_type="TurnStarted",
            payload={"prompt": "Hello", "turn_id": "t1"},
            created_at=time.time(),
        )
        turn_evt = map_daemon_event_to_turn_event(evt_start)
        self.assertIsInstance(turn_evt, TurnStarted)
        self.assertEqual(turn_evt.prompt, "Hello")

    async def test_02_tui_bridge_lifecycle_and_turn_execution(self):
        """Verify DaemonUIBridge connects, attaches to session, submits input, and receives mapped TurnEvents."""
        received_turn_events = []

        bridge = DaemonUIBridge(
            workspace_dir=str(self.root),
            token=self.server.token,
            event_sink=lambda e: received_turn_events.append(e),
        )

        ok = await bridge.connect()
        self.assertTrue(ok)

        attached = await bridge.attach(self.session_id)
        self.assertTrue(attached)

        submitted = await bridge.send_input("Turn through TUI Bridge")
        self.assertTrue(submitted)

        for _ in range(30):
            if any(isinstance(e, (TurnCompleted, TurnStarted)) for e in received_turn_events):
                break
            await asyncio.sleep(0.1)

        self.assertGreater(len(received_turn_events), 0)

        # Closing bridge must detach without stopping daemon server
        await bridge.close()
        self.assertTrue(self.server._running)
