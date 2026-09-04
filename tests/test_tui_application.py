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
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.runtime = KittRuntime.build(self.temp.name, RuntimeConfig(history_enabled=False, persistence_enabled=False))

    async def asyncTearDown(self):
        await self.runtime.aclose()
        self.temp.cleanup()

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

    async def test_prompt_history_navigation(self):
        with create_pipe_input() as pipe:
            ui = KittUIApp(self.runtime, "tui", input=pipe, output=DummyOutput(), no_animation=True)
            ui.build_application()
            task = asyncio.create_task(ui.run_async())
            await asyncio.sleep(0.05)

            # Insert first prompt into history
            ui.prompt_buffer.text = "first message"
            ui._accept_prompt(ui.prompt_buffer)
            await asyncio.sleep(0.05)

            # Insert second prompt into history
            ui.prompt_buffer.text = "second message"
            ui._accept_prompt(ui.prompt_buffer)
            await asyncio.sleep(0.05)

            # Press UP: should retrieve "second message"
            pipe.send_bytes(b"\x1b[A")
            await asyncio.sleep(0.05)
            self.assertEqual(ui.prompt_buffer.text, "second message")

            # Press UP again: should retrieve "first message"
            pipe.send_bytes(b"\x1b[A")
            await asyncio.sleep(0.05)
            self.assertEqual(ui.prompt_buffer.text, "first message")

            # Press DOWN: should navigate forward to "second message"
            pipe.send_bytes(b"\x1b[B")
            await asyncio.sleep(0.05)
            self.assertEqual(ui.prompt_buffer.text, "second message")

            pipe.send_bytes(b"\x1b\x04")  # Esc, Ctrl+D to exit
            await asyncio.wait_for(task, 2)

    async def test_ctrl_c_unblocks_prompt_submission(self):
        with create_pipe_input() as pipe:
            ui = KittUIApp(self.runtime, "tui", input=pipe, output=DummyOutput(), no_animation=True)
            ui.build_application()
            task = asyncio.create_task(ui.run_async())
            await asyncio.sleep(0.05)

            # Simulate thinking state
            ui.state.is_thinking = True
            ui.bridge._active_turn_id = "mock_turn_1"

            # Press Ctrl+C
            pipe.send_bytes(b"\x03")
            await asyncio.sleep(0.05)

            self.assertFalse(ui.state.is_thinking)
            self.assertFalse(ui.bridge.is_active)

            # Now verify submitting a new prompt works and does not fail
            ui.prompt_buffer.text = "new prompt"
            ui._accept_prompt(ui.prompt_buffer)
            self.assertEqual(ui.prompt_buffer.text, "")

            pipe.send_bytes(b"\x1b\x04")
            await asyncio.wait_for(task, 2)


if __name__ == "__main__":
    unittest.main()
