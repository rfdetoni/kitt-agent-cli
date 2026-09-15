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

    def test_transcript_mouse_scrolling_is_enabled_for_interactive_terminal(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            with KittRuntime.build(root_dir=tmp_dir) as runtime:
                app = KittUIApp(runtime=runtime)
                with patch("kitt.ui.layout._interactive_terminal", return_value=True):
                    app.build_application()

                self.assertTrue(app.mouse_support_enabled)
                app.transcript_window.vertical_scroll = 9
                app.state.follow_tail = True

                app._transcript_mouse_handler(
                    SimpleNamespace(event_type=MouseEventType.SCROLL_UP)
                )

                self.assertEqual(app.transcript_window.vertical_scroll, 6)
                self.assertFalse(app.state.follow_tail)

    def test_non_interactive_session_preserves_native_mouse_default(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            with KittRuntime.build(root_dir=tmp_dir) as runtime:
                app = KittUIApp(runtime=runtime)
                with patch("kitt.ui.layout._interactive_terminal", return_value=False):
                    app.build_application()

                self.assertFalse(app.mouse_support_enabled)


if __name__ == "__main__":
    unittest.main()
