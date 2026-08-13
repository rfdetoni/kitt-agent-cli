import asyncio
import tempfile
import unittest
from unittest.mock import MagicMock
from kitt.core.runtime import KittRuntime
from kitt.ui.app import KittUIApp

class TestCloseOverlayFocusSafety(unittest.TestCase):
    def test_close_overlay_handles_layout_focus_value_error_safely(self):
        async def run_test():
            with tempfile.TemporaryDirectory() as tmp_dir:
                runtime = KittRuntime.build(root_dir=tmp_dir)
                app = KittUIApp(runtime=runtime)
                app.build_application()

                app.open_overlay("help", app.help_control)
                self.assertEqual(app.state.active_overlay, "help")

                # Mock layout.focus to raise ValueError("Invalid value. Container does not appear in the layout.")
                app.application.layout.focus = MagicMock(side_effect=ValueError("Invalid value. Container does not appear in the layout."))

                # Closing overlay should not throw exception even if focus raises ValueError
                try:
                    app.close_overlay()
                except ValueError as e:
                    self.fail(f"close_overlay raised unexpected ValueError: {e}")

                self.assertIsNone(app.state.active_overlay)

        asyncio.run(run_test())

if __name__ == "__main__":
    unittest.main()
