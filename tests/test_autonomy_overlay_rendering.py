import asyncio
import tempfile
import unittest
from kitt.core.runtime import KittRuntime
from kitt.ui.app import KittUIApp

class TestAutonomyOverlayRendering(unittest.TestCase):
    def test_autonomy_overlay_opens_and_renders(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = KittRuntime.build(root_dir=tmp_dir)
            app = KittUIApp(runtime=runtime)

            # 1. Trigger open_overlay("autonomy_control")
            app.open_overlay("autonomy_control", app.autonomy_control)
            self.assertEqual(app.state.active_overlay, "autonomy_control")

            # 2. Render text
            text = app._autonomy_text()
            self.assertIn("CENTRAL DE PERMISSÕES & AUTONOMIA", text)
            self.assertIn("Supervisionado Estrito", text)
            self.assertIn("Edição Livre de Arquivos", text)

            # 3. Test changing preset to files_free
            runtime.autonomy_store.set_preset("files_free")
            runtime.processor.registry.policy.autonomy = runtime.autonomy_store.get()
            self.assertTrue(runtime.processor.registry.policy.autonomy.allow_file_write_auto)

            # 4. Close overlay
            app.close_overlay()
            self.assertIsNone(app.state.active_overlay)

if __name__ == "__main__":
    unittest.main()
