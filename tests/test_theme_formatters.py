import os
import unittest
from kitt.ui.theme import Theme
from kitt.ui.state import UIState, AgentTaskStep
from kitt.ui.components.sidebar import SidebarComponent

class TestThemeFormatters(unittest.TestCase):
    def test_theme_formatters_output(self):
        theme = Theme()
        os.environ.pop("NO_COLOR", None)
        err = theme.format_error("ERR")
        suc = theme.format_success("OK")
        warn = theme.format_warning("WARN")

        self.assertIn("ERR", err)
        self.assertIn("OK", suc)
        self.assertIn("WARN", warn)
        self.assertTrue(err.startswith("\033[38;2;"))
        self.assertTrue(suc.startswith("\033[38;2;"))
        self.assertTrue(warn.startswith("\033[38;2;"))

    def test_sidebar_render_with_error_task(self):
        state = UIState()
        state.active_tasks = [
            AgentTaskStep(id="t1", name="Failed Task", role="exec", status="error", summary="Task failed")
        ]
        sidebar = SidebarComponent()
        output = sidebar.render(state)
        self.assertIn("ERR", output)
        self.assertIn("Failed Task", output)

if __name__ == "__main__":
    unittest.main()
