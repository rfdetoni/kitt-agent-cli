import unittest

from kitt.ui.snapshot import render_snapshot
from kitt.ui.state import UIState


class TestSnapshots(unittest.TestCase):
    def test_home_and_responsive_session_snapshots(self):
        home = UIState(workspace_path="/work/kitt")
        for width, height in ((60, 20), (100, 30), (140, 40)):
            rendered = render_snapshot(home, width, height)
            self.assertIn("K.I.T.T.", rendered)
            self.assertIn("Ask K.I.T.T.", rendered)
            self.assertNotIn("\x1b", rendered)
        home.route = "session"
        home.sidebar_open = True
        home.append_message("user", "hello")
        home.append_message("assistant", "online")
        wide = render_snapshot(home, 140, 40)
        self.assertIn("USER: hello", wide)
        self.assertIn("SIDEBAR", wide)
        self.assertNotIn("SIDEBAR", render_snapshot(home, 60, 20))


if __name__ == "__main__":
    unittest.main()
