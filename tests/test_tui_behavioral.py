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
from kitt.core.turn_events import BudgetApplied, TextDelta, TurnCompleted, TurnStarted
from kitt.ui.app import KittUIApp
from kitt.ui.capabilities import create_backend
from kitt.ui.event_bridge import TurnEventBridge
from kitt.ui.fallback import PlainLineUI
from kitt.ui.state import UIState


class TestTUIBehavioralRequirements(unittest.IsolatedAsyncioTestCase):
    async def test_provider_managed_context_renders_webchat_without_local_percentage(self):
        from kitt.ui.components.sidebar import SidebarComponent
        from kitt.ui.reducer import reduce_ui_event
        from kitt.ui.snapshot import render_snapshot

        state = UIState()
        reduce_ui_event(state, BudgetApplied(total_input_tokens=50_000, reserved_output_tokens=0, window_size=0))
        text = SidebarComponent().render(state)
        self.assertIn("WebChat", text)
        self.assertIn("50000 tokens estimados", text)
        self.assertNotIn("100%", text)
        self.assertIn("WebChat", render_snapshot(state, 120, 30))

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.runtime = KittRuntime.build(self.tmp.name, RuntimeConfig(history_enabled=True, persistence_enabled=True))

    async def asyncTearDown(self):
        await self.runtime.aclose()
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
        turn_id = await bridge.start("Hi", "conv-1", no_history=False, mode="ask")
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
        await bridge.start("Oi", "conv-2", no_history=False, mode="ask")
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

    async def test_ctrl_x_a_opens_live_agent_dashboard(self):
        """The global agent chord must not be shadowed by the model editor."""
        with create_pipe_input() as pipe:
            ui = KittUIApp(self.runtime, "tui", input=pipe, output=DummyOutput(), no_animation=True)
            ui.build_application()
            task = asyncio.create_task(ui.run_async())
            try:
                await asyncio.sleep(0.05)
                pipe.send_text("\x18a")
                await asyncio.sleep(0.08)
                self.assertEqual(ui.state.active_overlay, "agents")
                self.assertIs(ui.application.layout.current_control, ui.agents_control)
            finally:
                ui.request_exit()
                await asyncio.wait_for(task, 2)

    async def test_permission_click_closes_modal_without_revoking_daemon_grant(self):
        opened = asyncio.Event()
        release = asyncio.Event()
        calls = []

        class PendingDaemon:
            daemon_mode = True
            is_active = False

            async def remember_approval(self, tool, scope, executable_identity=""):
                calls.append(("remember", tool, scope))
                return {"status": "ok"}

            async def resolve_approval(self, approval_id, allow):
                calls.append(("approve", approval_id, allow))
                opened.set()
                await release.wait()
                return {"status": "ok"}

        with create_pipe_input() as pipe:
            ui = KittUIApp(self.runtime, "tui", input=pipe, output=DummyOutput(), no_animation=True)
            ui.build_application()
            ui.bridge = PendingDaemon()
            ui.state.pending_approvals.append({
                "approval_id": "requested-once", "tool_name": "run_command",
                "turn_id": "turn", "conversation_id": "conv", "workspace_id": "ws",
                "action_hash": "test", "executable_identity": "model:testing"
            })
            ui.open_overlay("permission", ui.permission_control)
            task = asyncio.create_task(ui.resolve_approval("always_workspace"))
            await asyncio.wait_for(opened.wait(), 1)
            self.assertNotEqual(ui.state.active_overlay, "permission")
            self.assertEqual(len(ui.state.pending_approvals), 1)
            await ui.resolve_approval("always_workspace")
            self.assertEqual([c[0] for c in calls], ["remember", "approve"])
            release.set()
            await asyncio.wait_for(task, 1)
            self.assertFalse(ui.state.pending_approvals)
            self.assertNotEqual(ui.state.active_overlay, "permission")

    async def test_failed_daemon_approval_reopens_current_request(self):
        class FailedDaemon:
            daemon_mode = True
            is_active = False

            async def resolve_approval(self, approval_id, allow):
                raise RuntimeError("daemon refused approval")

        with create_pipe_input() as pipe:
            ui = KittUIApp(self.runtime, "tui", input=pipe, output=DummyOutput(), no_animation=True)
            ui.build_application()
            ui.bridge = FailedDaemon()
            ui.state.pending_approvals.append({
                "approval_id": "still-pending", "tool_name": "run_command"
            })
            ui.open_overlay("permission", ui.permission_control)
            await ui.resolve_approval("once")
            self.assertEqual(ui.state.active_overlay, "permission")
            self.assertEqual(ui.state.pending_approval["approval_id"], "still-pending")
            self.assertEqual(ui.state.status_text, "ERROR")

    async def test_workarea_mouse_and_scroll_are_independent_of_input_history(self):
        from kitt.ui.interaction import InteractionMap
        from kitt.ui.scroll import make_wheel_scroll_handler
        from prompt_toolkit.mouse_events import MouseEventType

        with create_pipe_input() as pipe:
            ui = KittUIApp(self.runtime, "tui", input=pipe, output=DummyOutput(), no_animation=True)
            ui.build_application()
            self.assertTrue(ui._mouse_capture_enabled())
            self.assertIsNone(ui.state.active_overlay)
        window = SimpleNamespace(
            vertical_scroll=10**9,
            render_info=SimpleNamespace(content_height=130, window_height=30)
        )
        wheel = make_wheel_scroll_handler(lambda: window, step=3)
        wheel(SimpleNamespace(event_type=MouseEventType.SCROLL_UP))
        self.assertEqual(window.vertical_scroll, 97)

        hits = InteractionMap()
        hits.add("permission", 0, 1, 8, "permission.action", "allow")
        self.assertIsNone(hits.release("permission", 3, 0))
        hits.press("permission", 3, 0)
        self.assertIsNotNone(hits.release("permission", 3, 0))

    async def test_04_create_backend_mode_plain(self):
        """Verify create_backend mode=plain returns PlainLineUI."""
        backend = create_backend(self.runtime, mode="plain")
        self.assertIsInstance(backend, PlainLineUI)

    async def test_05_sidebar_toggle(self):
        """Verify sidebar open toggle state."""
        with create_pipe_input() as pipe:
            app_ui = KittUIApp(self.runtime, "tui", input=pipe, output=DummyOutput(), no_animation=True)
            self.assertFalse(app_ui.state.sidebar_open)
    async def test_06_submit_enters_session_before_bridge_start_finishes(self):
        """A slow daemon/bootstrap must not leave the TUI looking frozen on home."""
        started = asyncio.Event()
        release = asyncio.Event()

        class SlowBridge:
            def __init__(self):
                self.is_active = False

            async def start(
                self,
                prompt,
                conversation_id,
                explicit_files=frozenset(),
                no_history=False,
                mode="auto",
            ):
                self.is_active = True
                started.set()
                await release.wait()
                self.is_active = False
                return "turn-ui-start"

        with create_pipe_input() as pipe:
            app_ui = KittUIApp(
                self.runtime,
                "tui",
                input=pipe,
                output=DummyOutput(),
                no_animation=True,
            )
            app_ui.bridge = SlowBridge()

            submit_task = asyncio.create_task(app_ui.submit("Implement feature"))
            await asyncio.wait_for(started.wait(), 1)

            self.assertEqual(app_ui.state.route, "session")
            self.assertTrue(app_ui.state.is_thinking)
            self.assertEqual(app_ui.state.status_text, "STARTING")
            self.assertEqual(
                [block.text for block in app_ui.state.transcript if block.kind == "user"],
                ["Implement feature"],
            )
            self.assertTrue(app_ui.state.active_tasks)

            app_ui._on_event(
                TurnStarted(
                    turn_id="turn-ui-start",
                    conversation_id=app_ui.state.active_conversation_id,
                    prompt="Implement feature",
                )
            )
            self.assertEqual(
                [block.text for block in app_ui.state.transcript if block.kind == "user"],
                ["Implement feature"],
            )
            self.assertEqual(app_ui.state.status_text, "SCANNING")

            release.set()
            await asyncio.wait_for(submit_task, 1)

    async def test_07_event_bridge_is_active_and_double_submit_guard(self):
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
