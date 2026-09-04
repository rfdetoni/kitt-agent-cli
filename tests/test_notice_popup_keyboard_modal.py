import tempfile
import unittest
from types import SimpleNamespace

from prompt_toolkit.keys import Keys

from kitt.core.runtime import KittRuntime
from kitt.ui.app import KittUIApp


class TestNoticePopupKeyboardModal(unittest.TestCase):
    def _active_eager_binding(self, app, key):
        matches = [
            binding
            for binding in app._key_bindings().bindings
            if binding.keys == (key,)
            and binding.filter()
            and binding.eager()
        ]
        self.assertTrue(matches, f"no active eager binding for {key}")
        return matches[-1]

    def test_enter_dismisses_notice_without_touching_composer(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            with KittRuntime.build(tmp) as runtime:
                app = KittUIApp(runtime)
                app.prompt_buffer.text = "must remain unsent"
                app.state.add_toast("message popup")

                binding = self._active_eager_binding(app, Keys.Enter)
                binding.handler(SimpleNamespace())

                self.assertEqual(app.state.active_toasts(), [])
                self.assertEqual(app.prompt_buffer.text, "must remain unsent")

    def test_escape_dismisses_notice_eagerly(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            with KittRuntime.build(tmp) as runtime:
                app = KittUIApp(runtime)
                app.prompt_buffer.text = "do not alter"
                app.state.add_toast("message popup")

                binding = self._active_eager_binding(app, Keys.Escape)
                binding.handler(SimpleNamespace())

                self.assertEqual(app.state.active_toasts(), [])
                self.assertEqual(app.prompt_buffer.text, "do not alter")


if __name__ == "__main__":
    unittest.main()
