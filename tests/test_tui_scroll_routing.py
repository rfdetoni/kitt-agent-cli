import tempfile
import unittest
from types import SimpleNamespace

from prompt_toolkit.mouse_events import MouseEventType

from kitt.core.runtime import KittRuntime
from kitt.ui.app import KittUIApp


class TestTUIScrollRouting(unittest.TestCase):
    EXPECTED_SCROLLABLES = {
        "transcript",
        "sidebar",
        "prompt",
        "permission",
        "palette",
        "model_setup",
        "provider_popup",
        "session_picker",
        "timeline",
        "diff",
        "agents",
        "autonomy",
        "reverse_proxy",
        "help",
    }

    def _build_ui(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        runtime = KittRuntime.build(root_dir=tmp.name)
        ui = KittUIApp(runtime=runtime)
        ui.build_application()
        self.addCleanup(runtime.close)
        self.addCleanup(tmp.cleanup)
        return ui

    def test_every_registered_panel_routes_wheel_only_to_itself(self):
        ui = self._build_ui()
        self.assertTrue(self.EXPECTED_SCROLLABLES.issubset(ui.scrollable_windows))

        event = SimpleNamespace(event_type=MouseEventType.SCROLL_DOWN)
        for target_name, target in ui.scrollable_windows.items():
            with self.subTest(panel=target_name):
                for window in ui.scrollable_windows.values():
                    window.vertical_scroll = 10
                before = {
                    name: int(window.vertical_scroll)
                    for name, window in ui.scrollable_windows.items()
                }

                target.content.mouse_handler(event)

                after = {
                    name: int(window.vertical_scroll)
                    for name, window in ui.scrollable_windows.items()
                }
                self.assertGreater(after[target_name], before[target_name])
                for other_name in before:
                    if other_name != target_name:
                        self.assertEqual(
                            after[other_name],
                            before[other_name],
                            f"scroll on {target_name} leaked into {other_name}",
                        )

    def test_plain_text_modal_scroll_anchor_tracks_requested_row(self):
        ui = self._build_ui()
        help_window = ui.scrollable_windows["help"]
        help_window.vertical_scroll = 7

        cursor = ui.help_control.get_cursor_position()

        self.assertIsNotNone(cursor)
        self.assertEqual(cursor.y, 7)

    def test_prompt_wheel_never_moves_transcript(self):
        ui = self._build_ui()
        ui.transcript_window.vertical_scroll = 25
        ui.prompt_window.vertical_scroll = 6

        ui.prompt_control.mouse_handler(
            SimpleNamespace(event_type=MouseEventType.SCROLL_UP)
        )

        self.assertEqual(ui.transcript_window.vertical_scroll, 25)
        self.assertEqual(ui.prompt_window.vertical_scroll, 3)

    def test_mouse_starts_enabled_and_can_be_disabled(self):
        ui = self._build_ui()
        self.assertTrue(ui.mouse_support_enabled)
        self.assertTrue(ui.state.mouse_enabled)
        self.assertFalse(ui.toggle_mouse_support())
        self.assertFalse(ui.state.mouse_enabled)


if __name__ == "__main__":
    unittest.main()
