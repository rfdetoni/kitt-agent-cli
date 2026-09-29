import asyncio
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from kitt.core.turn_events import TextDelta, TurnCancelled, TurnCompleted, TurnStarted
from kitt.ui.event_bridge import TurnEventBridge


class Processor:
    def run_turn(self, command):
        yield TurnStarted(turn_id=command.turn_id, conversation_id=command.conversation_id, prompt=command.prompt)
        for _ in range(1000):
            yield TextDelta(delta="x")
        yield TurnCompleted(response="x" * 1000)

    def cancel_turn(self, turn_id, reason, conversation_id=None):
        return iter(())


class BlockingProcessor:
    def __init__(self):
        self.first_started = threading.Event()
        self.release_first = threading.Event()

    def run_turn(self, command):
        yield TurnStarted(
            turn_id=command.turn_id,
            conversation_id=command.conversation_id,
            prompt=command.prompt,
        )
        if command.prompt == "first":
            self.first_started.set()
            self.release_first.wait(timeout=5)
            yield TurnCompleted(response="stale-first")
            return
        yield TurnCompleted(response=f"completed:{command.prompt}")

    def cancel_turn(self, turn_id, reason, conversation_id=None):
        yield TurnCancelled(reason=reason)


class OfflineDaemonBridge:
    def __init__(self):
        self.attached_session_id = None
        self.closed = False

    async def connect(self):
        return False

    async def close(self):
        self.closed = True


class ConnectedDaemonBridge:
    def __init__(self):
        self.attached_session_id = None
        self.closed = False
        self.calls = []

    async def connect(self):
        self.calls.append(("connect",))
        return True

    async def set_logging(self, level, path):
        self.calls.append(("set_logging", level, path))
        return {"status": "ok", "level": level, "path": path}

    async def attach(self, session_id):
        self.calls.append(("attach", session_id))
        self.attached_session_id = session_id
        return True

    async def set_reasoning(self, value):
        self.calls.append(("set_reasoning", value))
        return {"status": "ok"}

    async def set_autonomy(self, preset):
        self.calls.append(("set_autonomy", preset))
        return {"status": "ok"}

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

    async def test_cancelled_blocking_worker_does_not_block_next_prompt(self):
        events = []
        processor = BlockingProcessor()
        runtime = self._runtime()
        runtime.processor = processor
        bridge = TurnEventBridge(runtime, events.append, lambda: None)

        try:
            first_turn = await bridge.start("first", "conversation", no_history=True)
            started = await asyncio.wait_for(
                asyncio.to_thread(processor.first_started.wait, 1.0),
                timeout=1.5,
            )
            self.assertTrue(started)

            await bridge.cancel("User pressed Ctrl+C")
            self.assertFalse(bridge.is_active)

            second_turn = await bridge.start("second", "conversation", no_history=True)
            self.assertNotEqual(first_turn, second_turn)
            self.assertIsNotNone(bridge._consumer)
            await asyncio.wait_for(bridge._consumer, timeout=1.0)

            completed = [event.response for event in events if isinstance(event, TurnCompleted)]
            self.assertIn("completed:second", completed)
            self.assertNotIn("stale-first", completed)
            self.assertFalse(bridge.is_active)
        finally:
            processor.release_first.set()
            await asyncio.sleep(0.05)
            await bridge.shutdown()

    async def test_cancelled_consumer_cannot_clear_replacement_turn_state(self):
        bridge = TurnEventBridge(self._runtime(), lambda event: None, lambda: None)
        bridge._turn_generation = 1
        bridge._active_turn_id = "replacement"
        bridge._queue_signal = asyncio.Event()
        stale_consumer = asyncio.create_task(bridge._consume(0))
        stale_consumer.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await stale_consumer

        self.assertEqual(bridge.active_turn_id, "replacement")
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

    async def test_level_2_is_synchronized_to_existing_daemon(self):
        daemon = ConnectedDaemonBridge()
        runtime = self._runtime(daemon_enabled=True)
        runtime.canonical_root = "."
        bridge = TurnEventBridge(runtime, lambda event: None, lambda: None)

        with (
            patch("kitt.ui.daemon_bridge.DaemonUIBridge", return_value=daemon),
            patch.dict(
                os.environ,
                {
                    "KITT_LOG_LEVEL": "2",
                    "KITT_LOG_FILE": "/tmp/kitt-live-daemon.log",
                },
                clear=False,
            ),
        ):
            self.assertTrue(await bridge.ensure_daemon("conversation"))

        self.assertIn(
            ("set_logging", 2, "/tmp/kitt-live-daemon.log"),
            daemon.calls,
        )
        self.assertLess(
            daemon.calls.index(("set_logging", 2, "/tmp/kitt-live-daemon.log")),
            daemon.calls.index(("attach", "conversation")),
        )
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
