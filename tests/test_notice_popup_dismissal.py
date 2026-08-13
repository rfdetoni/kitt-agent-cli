import tempfile
import unittest
from kitt.ui.state import UIState
from kitt.core.runtime import KittRuntime
from kitt.ui.app import KittUIApp

class TestNoticePopupDismissal(unittest.TestCase):
    def test_toast_added_and_dismissed(self):
        state = UIState()
        state.add_toast("Perfil ativado: Supervisionado Estrito")
        self.assertEqual(len(state.active_toasts()), 1)

        # Clear toasts via clear_toasts()
        state.clear_toasts()
        self.assertEqual(len(state.active_toasts()), 0)

    def test_toast_rendering_text(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = KittRuntime.build(root_dir=tmp_dir)
            app = KittUIApp(runtime=runtime)
            app.state.add_toast("Aviso de teste")

            text = app._toast_text()
            self.assertIn("Aviso de teste", text)
            self.assertIn("Fechar Aviso", text)

            app.state.clear_toasts()
            self.assertEqual(app._toast_text(), "")

if __name__ == "__main__":
    unittest.main()
