import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from kitt.core.turn_events import TextDelta, TurnCompleted, TurnStarted
from kitt.ui.event_bridge import TurnEventBridge


class Processor:
    def run_turn(self, command):
        yield TurnStarted(turn_id=command.turn_id, conversation_id=command.conversation_id, prompt=command.prompt)
        for _ in range(1000):
            yield TextDelta(delta="x")
        yield TurnCompleted(response="x" * 1000)

    def cancel_turn(self, turn_id, reason, conversation_id=None):
        return iter(())


class OfflineDaemonBridge:
    def __init__(self):
        self.attached_session_id = None
        self.closed = False

    async def connect(self):
        return False

    async def close(self):
        self.closed = True


class TestTurnEventBridge(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _runtime(*, daemon_enabled=False, daemon_local_fallback=False):
        return SimpleNamespace(
            processor=Processor(),
            canonical_root=".",
            config=SimpleNamespace(
                daemon_enabled=daemon_enabled,
                daemon_auto_start=True,
                daemon_local_fallback=daemon_local_fallback,
            ),
            history=SimpleNamespace(repo=SimpleNamespace(save_message=lambda *a: None)),
        )

    async def test_coalesces_deltas_and_keeps_final_text(self):
        events, invalidations = [], []
        bridge = TurnEventBridge(
            self._runtime(), events.append, lambda: invalidations.append(1), max_queue=16
        )
        await bridge.start("hello", "conversation", no_history=True)
        await asyncio.wait_for(bridge._consumer, 2)
        text = "".join(event.delta for event in events if isinstance(event, TextDelta))
        self.assertEqual(len(text), 1000)
        self.assertLess(len(invalidations), 1000)
        self.assertLessEqual(bridge._queue.maxsize, 128)
        await bridge.shutdown()

    async def test_ola_uses_local_processor_when_daemon_never_spawned(self):
        events = []
        daemon = OfflineDaemonBridge()
        bridge = TurnEventBridge(self._runtime(daemon_enabled=True), events.append, lambda: None)
        bootstrap_failure = {
            "status": "error",
            "error": "[Errno 2] No such file or directory",
            "bootstrap_failed": True,
            "spawned": False,
            "errno": 2,
        }

        with (
            patch("kitt.ui.daemon_bridge.DaemonUIBridge", return_value=daemon),
            patch.object(
                bridge,
                "_start_daemon_process",
                new=AsyncMock(return_value=bootstrap_failure),
            ),
        ):
            await bridge.start("ola", "conversation", no_history=True)
            await asyncio.wait_for(bridge._consumer, 2)

        self.assertTrue(any(isinstance(event, TurnCompleted) for event in events))
        self.assertFalse(bridge.daemon_mode)
        self.assertTrue(daemon.closed)
        await bridge.shutdown()

    async def test_post_spawn_daemon_failure_stays_fail_closed(self):
        daemon = OfflineDaemonBridge()
        bridge = TurnEventBridge(self._runtime(daemon_enabled=True), lambda event: None, lambda: None)
        post_spawn_failure = {
            "status": "error",
            "error": "daemon did not become ready",
            "spawned": True,
        }

        with (
            patch("kitt.ui.daemon_bridge.DaemonUIBridge", return_value=daemon),
            patch.object(
                bridge,
                "_start_daemon_process",
                new=AsyncMock(return_value=post_spawn_failure),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "daemon did not become ready"):
                await bridge.ensure_daemon("conversation")

        self.assertTrue(daemon.closed)
        await bridge.shutdown()

    def test_only_explicit_pre_spawn_marker_is_safe_for_implicit_local_fallback(self):
        self.assertTrue(
            TurnEventBridge._safe_pre_spawn_failure(
                {"status": "error", "bootstrap_failed": True, "spawned": False}
            )
        )
        self.assertFalse(
            TurnEventBridge._safe_pre_spawn_failure(
                {"status": "error", "bootstrap_failed": True, "spawned": True}
            )
        )
        self.assertFalse(
            TurnEventBridge._safe_pre_spawn_failure(
                {"status": "error", "spawned": False}
            )
        )


if __name__ == "__main__":
    unittest.main()
