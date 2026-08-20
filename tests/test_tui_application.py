import asyncio
import tempfile
import unittest

from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.ui.app import KittUIApp


class TestTUIApplication(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = KittRuntime.build(self.tmp.name, RuntimeConfig(history_enabled=False, persistence_enabled=False))

    async def asyncTearDown(self):
        await self.runtime.aclose()
        self.tmp.cleanup()

    async def test_full_screen_palette_focus_and_clean_exit(self):
        with create_pipe_input() as pipe:
            ui = KittUIApp(self.runtime, "tui", input=pipe, output=DummyOutput(), no_animation=True)
            application = ui.build_application()
            self.assertTrue(application.full_screen)
            task = asyncio.create_task(ui.run_async())
            await asyncio.sleep(0.05)
            pipe.send_bytes(b"\x10")  # Ctrl+P
            await asyncio.sleep(0.05)
            self.assertEqual(ui.state.active_overlay, "palette")
            self.assertIs(application.layout.current_control, ui.palette_search_control)
            pipe.send_bytes(b"\x1b\x04")  # Esc, Ctrl+D
            self.assertEqual(await asyncio.wait_for(task, 2), 0)

    def test_application_has_real_retained_layout(self):
        with create_pipe_input() as pipe:
            ui = KittUIApp(self.runtime, "tui", input=pipe, output=DummyOutput(), no_animation=True)
            app = ui.build_application()
            self.assertEqual(type(app.layout.container).__name__, "FloatContainer")
            self.assertNotIn("PromptSession", type(app).__name__)
            self.assertEqual(app.cursor.get_cursor_shape(app), CursorShape.BLINKING_BEAM)

    async def test_home_scanner_moves_when_idle(self):
        with create_pipe_input() as pipe:
            ui = KittUIApp(self.runtime, "tui", input=pipe, output=DummyOutput())
            ui.build_application()
            animation = asyncio.create_task(ui._animate())
            start = ui.state.scanner_step
            await asyncio.sleep(0.12)
            self.assertGreater(ui.state.scanner_step, start)
            await ui.shutdown()
            await asyncio.gather(animation, return_exceptions=True)

    async def test_palette_command_selection_executes_legacy_command(self):
        with create_pipe_input() as pipe:
            ui = KittUIApp(self.runtime, "tui", input=pipe, output=DummyOutput(), no_animation=True)
            application = ui.build_application()
            task = asyncio.create_task(ui.run_async())
            await asyncio.sleep(0.05)
            pipe.send_bytes(b"\x10/memory\r")
            await asyncio.sleep(0.15)
            self.assertIsNone(ui.state.active_overlay)
            self.assertTrue(any("Global Memory" in block.text or "Project Memory" in block.text for block in ui.state.transcript))
            pipe.send_bytes(b"\x04")
            await asyncio.wait_for(task, 2)

    def test_home_text_formatting_no_leading_padding(self):
        ui = KittUIApp(self.runtime, "tui", input=DummyOutput(), output=DummyOutput(), no_animation=True)
        items = ui._home_text()
        # Ensure texts do not have arbitrary leading whitespace interfering with WindowAlign.CENTER
        for style_cls, text in items:
            stripped_line = text.lstrip("\n")
            self.assertFalse(stripped_line.startswith(" "), f"Unexpected leading spaces in '{text}'")


if __name__ == "__main__":
    unittest.main()
