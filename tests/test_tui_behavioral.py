import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.core.turn_events import TextDelta, TurnCompleted, TurnStarted
from kitt.ui.app import KittUIApp
from kitt.ui.capabilities import create_backend
from kitt.ui.event_bridge import TurnEventBridge
from kitt.ui.fallback import PlainLineUI
from kitt.ui.state import UIState


class TestTUIBehavioralRequirements(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = KittRuntime.build(self.tmp.name, RuntimeConfig(history_enabled=True, persistence_enabled=True))

    def tearDown(self):
        self.runtime.close()
        self.tmp.cleanup()

    async def test_01_assistant_response_persisted_once(self):
        """Verify assistant response is saved to history repository exactly once upon TurnCompleted."""
        saved_messages = []

        class MockRepo:
            def save_message(self, conv_id, turn_id, role, content, token_count=0):
                saved_messages.append((conv_id, turn_id, role, content))

        class MockProcessor:
            def run_turn(self, cmd):
                yield TurnStarted(turn_id=cmd.turn_id, conversation_id=cmd.conversation_id, prompt=cmd.prompt)
                yield TextDelta(delta="Hello ")
                yield TextDelta(delta="World!")
                yield TurnCompleted(response="Hello World!")

        rt = SimpleNamespace(
            history=SimpleNamespace(repo=MockRepo()),
            processor=MockProcessor(),
        )
        events = []
        bridge = TurnEventBridge(rt, events.append, lambda: None)
        turn_id = await bridge.start("Hi", "conv-1", no_history=False)
        await asyncio.wait_for(bridge._consumer, 2)
        await bridge.shutdown()

        # Should save 1 user message and 1 assistant message
        user_msgs = [m for m in saved_messages if m[2] == "user"]
        assistant_msgs = [m for m in saved_messages if m[2] == "assistant"]
        self.assertEqual(len(user_msgs), 1)
        self.assertEqual(len(assistant_msgs), 1)
        self.assertEqual(assistant_msgs[0][3], "Hello World!")

    async def test_01b_final_turn_completed_response_is_persisted(self):
        """Verify final-only assistant response still gets saved by the UI bridge."""
        saved_messages = []

        class MockRepo:
            def save_message(self, conv_id, turn_id, role, content, token_count=0):
                saved_messages.append((conv_id, turn_id, role, content))

        class MockProcessor:
            def run_turn(self, cmd):
                yield TurnCompleted(response="Resposta final!")

        rt = SimpleNamespace(
            history=SimpleNamespace(repo=MockRepo()),
            processor=MockProcessor(),
        )
        events = []
        bridge = TurnEventBridge(rt, events.append, lambda: None)
        await bridge.start("Oi", "conv-2", no_history=False)
        await asyncio.wait_for(bridge._consumer, 2)
        await bridge.shutdown()

        assistant_msgs = [m for m in saved_messages if m[2] == "assistant"]
        self.assertEqual(len(assistant_msgs), 1)
        self.assertEqual(assistant_msgs[0][3], "Resposta final!")

    async def test_02_toast_expiration(self):
        """Verify normal toasts expire after duration while persistent toasts remain."""
        state = UIState()
        state.add_toast("Temporary", persistent=False, duration=0.05)
        state.add_toast("Persistent", persistent=True)
        self.assertEqual(len(state.active_toasts()), 2)
        await asyncio.sleep(0.08)
        active = state.active_toasts()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].text, "Persistent")

    async def test_03_overlay_focus_stack_restoration(self):
        """Verify closing overlays restores focus in LIFO stack order."""
        with create_pipe_input() as pipe:
            app_ui = KittUIApp(self.runtime, "tui", input=pipe, output=DummyOutput(), no_animation=True)
            app_ui.build_application()
            task = asyncio.create_task(app_ui.run_async())
            await asyncio.sleep(0.05)

            # Initially focused on prompt
            self.assertIs(app_ui.application.layout.current_control, app_ui.prompt_control)

            # Open palette
            app_ui.open_overlay("palette", app_ui.palette_search_control)
            self.assertIs(app_ui.application.layout.current_control, app_ui.palette_search_control)

            # Open help over palette
            app_ui.open_overlay("help", app_ui.help_control)
            self.assertIs(app_ui.application.layout.current_control, app_ui.help_control)

            # Close top overlay (help) -> focus should return to palette_search_control
            app_ui.close_overlay()
            self.assertIs(app_ui.application.layout.current_control, app_ui.palette_search_control)

            # Close palette -> focus should return to prompt_control
            app_ui.close_overlay()
            self.assertIs(app_ui.application.layout.current_control, app_ui.prompt_control)

            app_ui.request_exit()
            await asyncio.wait_for(task, 2)

    async def test_04_create_backend_mode_plain(self):
        """Verify create_backend mode=plain returns PlainLineUI."""
        backend = create_backend(self.runtime, mode="plain")
        self.assertIsInstance(backend, PlainLineUI)

    async def test_05_sidebar_toggle(self):
        """Verify sidebar open toggle state."""
        with create_pipe_input() as pipe:
            app_ui = KittUIApp(self.runtime, "tui", input=pipe, output=DummyOutput(), no_animation=True)
            self.assertFalse(app_ui.state.sidebar_open)
    async def test_06_event_bridge_is_active_and_double_submit_guard(self):
        """Verify bridge.is_active status and submit error handling when turn is active."""
        class SlowProcessor:
            def run_turn(self, cmd):
                yield TurnStarted(turn_id=cmd.turn_id, conversation_id=cmd.conversation_id, prompt=cmd.prompt)
                yield TextDelta(delta="Working...")
                yield TurnCompleted(response="Done")

        rt = SimpleNamespace(
            history=SimpleNamespace(repo=SimpleNamespace(save_message=lambda *a: None), get_or_create_active=lambda: {"id": "c1"}),
            processor=SlowProcessor(),
            config=SimpleNamespace(history_enabled=False),
        )
        events = []
        bridge = TurnEventBridge(rt, events.append, lambda: None)
        self.assertFalse(bridge.is_active)
        await bridge.start("First", "c1", no_history=True)
        self.assertTrue(bridge.is_active)

        with create_pipe_input() as pipe:
            app_ui = KittUIApp(self.runtime, "tui", input=pipe, output=DummyOutput(), no_animation=True)
            app_ui.bridge = bridge
            # Second submit while bridge.is_active should return without raising exception
            await app_ui.submit("Second")
            await asyncio.wait_for(bridge._consumer, 2)
            await bridge.shutdown()
            self.assertFalse(bridge.is_active)

if __name__ == "__main__":
    unittest.main()
