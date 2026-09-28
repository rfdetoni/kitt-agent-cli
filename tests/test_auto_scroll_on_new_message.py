import tempfile
import unittest
from prompt_toolkit.data_structures import Point
from kitt.core.runtime import KittRuntime
from kitt.ui.app import KittUIApp

class TestAutoScrollOnNewMessage(unittest.TestCase):
    def test_cursor_position_updates_with_transcript_growth(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            with KittRuntime.build(root_dir=tmp_dir) as runtime:
                app = KittUIApp(runtime=runtime)
                app.build_application()

            # Initially empty transcript
            pos1 = app._transcript_cursor_position()
            self.assertIsInstance(pos1, Point)
            self.assertEqual(pos1.y, 0)

            # Add user message
            app.state.append_message("user", "Pergunta do usuário")
            pos2 = app._transcript_cursor_position()
            self.assertGreater(pos2.y, pos1.y)

            # Add assistant streaming response
            app.state.append_message("assistant", "Resposta linha 1\nResposta linha 2\nResposta linha 3")
            pos3 = app._transcript_cursor_position()
            self.assertGreater(pos3.y, pos2.y)

            # When follow_tail is False, retain a concrete cursor at the manual
            # scroll row so prompt_toolkit can move both up and down reliably.
            app.state.follow_tail = False
            app.transcript_window.vertical_scroll = 1
            manual = app._transcript_cursor_position()
            self.assertIsInstance(manual, Point)
            self.assertEqual(manual.y, 1)

if __name__ == "__main__":
    unittest.main()
