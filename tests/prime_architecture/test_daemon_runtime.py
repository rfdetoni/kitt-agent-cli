import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from kitt.daemon.client import DaemonClient
from kitt.daemon.server import DaemonServer
from kitt.history.database import HistoryDatabase


class FakeClient:
    def __init__(self, text: str):
        self.text = text

    def chat(self, *args, **kwargs):
        return self.text

    def chat_stream(self, *args, **kwargs):
        for chunk in (self.text[:4], self.text[4:]):
            yield chunk


class TestDaemonRuntime(unittest.IsolatedAsyncioTestCase):
    """End-to-end integration tests for persistent DaemonServer and multiplexed DaemonClient."""

    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        (self.root / "src").mkdir(parents=True, exist_ok=True)
        (self.root / "src" / "main.py").write_text("def hello(): pass\n", encoding="utf-8")

        self.server = DaemonServer(
            workspace_root=str(self.root),
            context_client=FakeClient('{"intent":"ASK","confidence":1.0}'),
            execution_client=FakeClient("Hello from KITT Daemon"),
        )
        await self.server.start()

        # Create session under real runtime workspace identity
        rt = await self.server._get_or_create_runtime()
        conv = rt.history.new_conversation("Session One")
        self.session_id = conv["id"]

    async def asyncTearDown(self):
        await self.server.stop()
        self.temp_dir.cleanup()

    async def test_01_daemon_real_turn_executes_and_emits_events(self):
        """Verify DaemonServer executes turn and emits real stream of turn events."""
        client = DaemonClient(workspace_root=str(self.root), token=self.server.token)
        connected = await client.connect()
        self.assertTrue(connected)

        received_events = []

        def _on_event(evt):
            received_events.append(evt)

        # Attach to session
        attach_res = await client.attach(self.session_id, on_event=_on_event)
        self.assertEqual(attach_res.get("status"), "ok")

        # Send input
        submitted = await client.send_input(self.session_id, "Hello KITT")
        self.assertTrue(submitted)

        # Wait for events to be processed and emitted
        for _ in range(30):
            if any(e.event_type in ("TurnCompleted", "TurnFailed") for e in received_events):
                break
            await asyncio.sleep(0.1)

        event_types = [e.event_type for e in received_events]
        self.assertIn("TurnStarted", event_types)
        await client.close()

    async def test_02_create_session_and_session_isolation(self):
        """Verify explicit session creation and isolation between multiple sessions."""
        client = DaemonClient(workspace_root=str(self.root), token=self.server.token)
        await client.connect()

        # Create session explicitly
        create_res = await client.send_request("create_session", {"title": "Isolated Session"})
        self.assertEqual(create_res.get("status"), "ok")
        new_session_id = create_res.get("session_id")
        self.assertIsNotNone(new_session_id)

        # Attach and verify isolation
        events = []
        await client.attach(new_session_id, on_event=lambda e: events.append(e))
        await client.send_input(new_session_id, "Test Isolation")

        for _ in range(30):
            if any(e.event_type in ("TurnCompleted", "TurnFailed") for e in events):
                break
            await asyncio.sleep(0.1)

        # All received events must belong to new_session_id
        self.assertGreater(len(events), 0)
        for e in events:
            self.assertEqual(e.session_id, new_session_id)

        await client.close()

    async def test_03_incremental_replay_on_reconnect(self):
        """Verify client reconnecting with last_sequence retrieves only new events."""
        client1 = DaemonClient(workspace_root=str(self.root), token=self.server.token)
        await client1.connect()
        await client1.attach(self.session_id)
        await client1.send_input(self.session_id, "Turn for replay")

        await asyncio.sleep(0.5)
        await client1.close()

        # Connect client 2 and verify replay
        client2 = DaemonClient(workspace_root=str(self.root), token=self.server.token)
        await client2.connect()
        replayed_res = await client2.attach(self.session_id, last_sequence=0)
        replayed = replayed_res.get("events", [])
        self.assertGreater(len(replayed), 0)

        highest_seq = max(e.sequence_id for e in replayed)
        # Re-attach asking for events after highest_seq
        empty_res = await client2.attach(self.session_id, last_sequence=highest_seq)
        empty_replay = empty_res.get("events", [])
        self.assertEqual(len(empty_replay), 0)

        await client2.close()
