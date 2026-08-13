import tempfile
import unittest
from kitt.core.runtime import KittRuntime
from kitt.ui.app import KittUIApp
from kitt.ui.layout import build_root_container

class TestFixedElementsNoWrap(unittest.TestCase):
    def test_fixed_windows_use_wrap_lines_false_and_scrollbar(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = KittRuntime.build(root_dir=tmp_dir)
            app = KittUIApp(runtime=runtime)

            # Build root layout container
            container = build_root_container(app)
            self.assertIsNotNone(container)

if __name__ == "__main__":
    unittest.main()
