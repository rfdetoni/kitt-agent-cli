import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.ui.app import KittUIApp
from kitt.ui.commands import CommandRegistry


class TestTUIRemoteCommands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        Path(self.temp.name, "sample.txt").write_text("sample", encoding="utf-8")
        self.runtime = KittRuntime.build(
            self.temp.name,
            RuntimeConfig(history_enabled=True, persistence_enabled=True, daemon_local_fallback=True),
        )
        self.input_cm = create_pipe_input()
        self.pipe = self.input_cm.__enter__()
        self.ui = KittUIApp(self.runtime, "tui", input=self.pipe, output=DummyOutput(), no_animation=True)
        self.ui.build_application()
        self.task = asyncio.create_task(self.ui.run_async())
        await asyncio.sleep(0.05)

    async def asyncTearDown(self):
        self.ui.request_exit()
        await asyncio.wait_for(self.task, 2)
        await self.runtime.aclose()
        self.input_cm.__exit__(None, None, None)
        self.temp.cleanup()

    def test_remote_command_registered_in_palette(self):
        registry = CommandRegistry()
        matches = registry.search("/remote")
        self.assertTrue(any(c.id == "remote" for c in matches))
        web_matches = registry.search("/web")
        self.assertTrue(any(c.id == "remote" for c in web_matches))

    async def test_remote_start_status_code_stop_lifecycle(self):
        with patch("kitt.ui.remote_commands.start_daemon_detached", return_value={"status": "ok", "pid": 1234}):
            # 1. Start remote
            await self.ui._execute_command("/remote 8765")
            self.assertIsNotNone(self.ui._remote_server)
            self.assertEqual(self.ui._remote_server.config.port, 8765)

            # 2. Status
            await self.ui._execute_command("/remote status")
            self.assertIn("ONLINE", self.ui.state.transcript[-1].text)

            # 3. Rotate code
            old_code = self.ui._remote_server.auth.pairing_code
            await self.ui._execute_command("/remote code")
            new_code = self.ui._remote_server.auth.pairing_code
            self.assertNotEqual(old_code, new_code)
            self.assertIn(new_code, self.ui.state.transcript[-1].text)

            # 4. Stop
            await self.ui._execute_command("/remote stop")
            self.assertIsNone(self.ui._remote_server)
            self.assertIn("encerrado com sucesso", self.ui.state.transcript[-1].text)

            # 5. Status after stop
            await self.ui._execute_command("/remote status")
            self.assertIn("DESATIVADO", self.ui.state.transcript[-1].text)

    async def test_remote_daemon_start_failure(self):
        with patch("kitt.ui.remote_commands.start_daemon_detached", return_value={"status": "error", "error": "Daemon test failure"}):
            await self.ui._execute_command("/remote 8767")
            self.assertIsNone(self.ui._remote_server)
            self.assertIn("Falha ao iniciar daemon", self.ui.state.transcript[-1].text)
            self.assertIn("Daemon test failure", self.ui.state.transcript[-1].text)

    async def test_remote_lan_flag(self):
        with patch("kitt.ui.remote_commands.start_daemon_detached", return_value={"status": "ok", "pid": 1234}):
            await self.ui._execute_command("/remote lan 8766")
            try:
                self.assertIsNotNone(self.ui._remote_server)
                self.assertEqual(self.ui._remote_server.config.host, "0.0.0.0")
                self.assertEqual(self.ui._remote_server.config.port, 8766)
            finally:
                if self.ui._remote_server:
                    self.ui._remote_server.stop()
                    self.ui._remote_server = None


