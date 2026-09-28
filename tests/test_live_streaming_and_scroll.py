import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from prompt_toolkit.mouse_events import MouseEventType

from kitt import KITT_VERSION
from kitt.core.runtime import KittRuntime
from kitt.ui.app import KittUIApp
from kitt.ui.layout import _version_text
from kitt.core.turn_events import TurnStarted, TextDelta, TurnCompleted


class TestLiveStreamingAndScroll(unittest.TestCase):
    def test_on_event_triggers_invalidate(self):
        async def run_test():
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
                with KittRuntime.build(root_dir=tmp_dir) as runtime:
                    app = KittUIApp(runtime=runtime)
                    app.build_application()

                app.application.invalidate = MagicMock()

                # 1. Simulate TurnStarted event
                app._on_event(TurnStarted(turn_id="t1", conversation_id="c1", prompt="Olá KITT"))
                app.application.invalidate.assert_called()

                # 2. Simulate streaming TextDelta
                app.application.invalidate.reset_mock()
                app._on_event(TextDelta(delta="Olá! Como posso ajudar?"))
                app.application.invalidate.assert_called()

                # 3. Check transcript content
                self.assertIn("Olá! Como posso ajudar?", app.state.transcript[-1].text)

                # 4. Simulate TurnCompleted
                app._on_event(TurnCompleted(response="Olá! Como posso ajudar?"))
                self.assertFalse(app.state.is_thinking)

        asyncio.run(run_test())

    def test_footer_displays_agent_cli_version(self):
        self.assertEqual(_version_text().strip(), f"KITT Agent CLI v{KITT_VERSION}")

    def test_tui_mouse_is_enabled_by_default(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            with KittRuntime.build(root_dir=tmp_dir) as runtime:
                app = KittUIApp(runtime=runtime)
                app.build_application()

                self.assertTrue(app.mouse_support_enabled)
                self.assertTrue(app.state.mouse_enabled)

    def test_transcript_mouse_scrolling_remains_available_when_explicitly_enabled(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            with KittRuntime.build(root_dir=tmp_dir) as runtime:
                app = KittUIApp(runtime=runtime)
                app.build_application()

                app.transcript_window.vertical_scroll = 9
                app.state.follow_tail = True

                app.transcript_control.mouse_handler(
                    SimpleNamespace(event_type=MouseEventType.SCROLL_UP)
                )

                self.assertEqual(app.transcript_window.vertical_scroll, 6)
                self.assertFalse(app.state.follow_tail)

    def test_transcript_cursor_tracks_manual_scroll_and_allows_scroll_down(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            with KittRuntime.build(root_dir=tmp_dir) as runtime:
                app = KittUIApp(runtime=runtime)
                app.build_application()

                app.state.transcript = [object()]
                app.state.follow_tail = False
                app.transcript_window.vertical_scroll = 6
                app._transcript_text = lambda: [("", "\n".join(f"line {i}" for i in range(30)))]

                cursor = app._transcript_cursor_position()
                self.assertEqual(cursor.y, 6)

                app.transcript_control.mouse_handler(
                    SimpleNamespace(event_type=MouseEventType.SCROLL_DOWN)
                )
                self.assertEqual(app.transcript_window.vertical_scroll, 9)
                self.assertEqual(app._transcript_cursor_position().y, 9)

    def test_idle_scanner_animation_advances(self):
        async def run_test():
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
                with KittRuntime.build(root_dir=tmp_dir) as runtime:
                    app = KittUIApp(runtime=runtime)
                    app.build_application()

                async def stop_after_first_tick(_delay):
                    app._shutdown = True

                with patch("kitt.ui.app.asyncio.sleep", new=stop_after_first_tick):
                    await app._animate()

                self.assertEqual(app.state.scanner_step, 1)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
