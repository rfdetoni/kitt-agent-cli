import os
import unittest
from kitt.ui.theme import Theme, strip_ansi
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

    def test_high_contrast_theme_is_opt_in_and_text_first(self):
        previous = os.environ.get("KITT_HIGH_CONTRAST")
        try:
            os.environ["KITT_HIGH_CONTRAST"] = "1"
            styles = Theme().style_dict()
            self.assertEqual(styles["background"], "bg:#000000 #FFFFFF")
            self.assertEqual(styles["selection"], "bg:#FFFFFF #000000 bold")
        finally:
            if previous is None:
                os.environ.pop("KITT_HIGH_CONTRAST", None)
            else:
                os.environ["KITT_HIGH_CONTRAST"] = previous

    def test_no_color_takes_precedence_over_high_contrast(self):
        previous_color = os.environ.get("NO_COLOR")
        previous_contrast = os.environ.get("KITT_HIGH_CONTRAST")
        try:
            os.environ["NO_COLOR"] = "1"
            os.environ["KITT_HIGH_CONTRAST"] = "1"
            self.assertEqual(Theme().style_dict()["background"], "")
        finally:
            if previous_color is None:
                os.environ.pop("NO_COLOR", None)
            else:
                os.environ["NO_COLOR"] = previous_color
            if previous_contrast is None:
                os.environ.pop("KITT_HIGH_CONTRAST", None)
            else:
                os.environ["KITT_HIGH_CONTRAST"] = previous_contrast

    def test_strip_ansi_removes_csi_sequences(self):
        raw = "\033[38;2;227;27;35mKITT\033[0m"
        self.assertEqual(strip_ansi(raw), "KITT")
        self.assertNotIn("\x1b", strip_ansi(raw))

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
