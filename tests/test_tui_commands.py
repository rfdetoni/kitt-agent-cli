import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.router.router import TaskRouter
from kitt.ui.app import KittUIApp


class TestTUICommands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        Path(self.temp.name, "sample.txt").write_text("sample", encoding="utf-8")
        self.runtime = KittRuntime.build(self.temp.name, RuntimeConfig(history_enabled=True, persistence_enabled=True))
        self.input_cm = create_pipe_input()
        self.pipe = self.input_cm.__enter__()
        self.ui = KittUIApp(self.runtime, "tui", input=self.pipe, output=DummyOutput(), no_animation=True)
        self.ui.build_application()
        self.task = asyncio.create_task(self.ui.run_async())
        await asyncio.sleep(0.05)

    async def asyncTearDown(self):
        self.ui.request_exit()
        await asyncio.wait_for(self.task, 2)
        self.runtime.close()
        self.input_cm.__exit__(None, None, None)
        self.temp.cleanup()

    async def test_model_roles_update_runtime_and_persist(self):
        await self.ui._execute_command("/model principal main-test")
        await self.ui._execute_command("/model context context-test")
        router = self.runtime.processor.router
        self.assertEqual(router.resolve_profile_for_task("code-generation")[1].model, "main-test")
        self.assertEqual(router.resolve_profile_for_task("context-gather")[1].model, "context-test")
        reloaded = TaskRouter(self.temp.name)
        self.assertEqual(reloaded.resolve_profile_for_task("code-generation")[1].model, "main-test")
        self.assertEqual(reloaded.resolve_profile_for_task("context-gather")[1].model, "context-test")
        self.assertEqual(self.ui.state.large_model, "main-test")
        self.assertEqual(self.ui.state.small_model, "context-test")
        self.assertGreaterEqual(router.resolve_profile_for_task("code-generation")[1].max_output_tokens, 2048)

    async def test_model_role_can_use_another_provider(self):
        await self.ui._execute_command("/model context openai gpt-4o https://example.test")
        profile = self.runtime.processor.router.resolve_profile_for_task("context-gather")[1]
        self.assertEqual((profile.backend, profile.model, profile.base_url), ("openai", "gpt-4o", "https://example.test"))
        reloaded = TaskRouter(self.temp.name).resolve_profile_for_task("context-gather")[1]
        self.assertEqual((reloaded.backend, reloaded.model, reloaded.base_url), ("openai", "gpt-4o", "https://example.test"))

    async def test_roles_can_use_two_ollama_endpoints(self):
        await self.ui._execute_command("/model principal ollama dev-model http://localhost:11434")
        await self.ui._execute_command("/model context ollama server-model http://ollama.internal:11434")
        router = self.runtime.processor.router
        principal = router.resolve_profile_for_task("code-generation")[1]
        context = router.resolve_profile_for_task("context-gather")[1]
        self.assertEqual((principal.backend, principal.model, principal.base_url), ("ollama", "dev-model", "http://localhost:11434"))
        self.assertEqual((context.backend, context.model, context.base_url), ("ollama", "server-model", "http://ollama.internal:11434"))

    async def test_model_all_updates_every_role(self):
        await self.ui._execute_command("/model all openai gpt-4o-mini")
        for task in ("context-gather", "code-generation", "validate-diff"):
            profile = self.runtime.processor.router.resolve_profile_for_task(task)[1]
            self.assertEqual((profile.backend, profile.model), ("openai", "gpt-4o-mini"))

    async def test_model_configuration_does_not_leak_to_new_runtime(self):
        await self.ui._execute_command("/model all openai gpt-4o-mini")
        with tempfile.TemporaryDirectory() as root:
            other = KittRuntime.build(root, RuntimeConfig(history_enabled=False, persistence_enabled=False))
            try:
                profile = other.processor.router.resolve_profile_for_task("code-generation")[1]
                self.assertEqual((profile.backend, profile.model), ("ollama", "qwen2.5:32b-instruct"))
            finally:
                other.close()

    async def test_model_overlay_assigns_selected_model(self):
        with patch("kitt.router.model_selector.ModelConfigurator.fetch_ollama_models", return_value=["one", "two"]):
            await self.ui._execute_command("/setup-models")
        self.assertEqual(self.ui.state.active_overlay, "model_setup")
        self.ui.model_setup_model.model_index = self.ui.model_setup_model.models.index("two")
        await self.ui._apply_selected_model()
        self.assertEqual(self.runtime.processor.router.resolve_profile_for_task("code-generation")[1].model, "two")
        # Overlay remains open to allow setting other roles sequentially
        self.assertEqual(self.ui.state.active_overlay, "model_setup")
        self.ui.close_overlay()
        self.assertIsNone(self.ui.state.active_overlay)

    async def test_setup_models_discovers_and_saves_remote_ollama(self):
        with patch("kitt.router.model_selector.ModelConfigurator.fetch_ollama_models", return_value=["remote-model"]) as fetch:
            await self.ui._execute_command("/setup-models http://ollama.internal:11434")
        self.assertEqual(fetch.call_args.args[0], "http://ollama.internal:11434")
        self.assertEqual(self.ui.model_setup_model.selected_provider, "ollama")
        self.ui.model_setup_model.model_index = self.ui.model_setup_model.models.index("remote-model")
        await self.ui._apply_selected_model()
        profile = self.runtime.processor.router.resolve_profile_for_task("code-generation")[1]
        self.assertEqual((profile.backend, profile.model, profile.base_url), ("ollama", "remote-model", "http://ollama.internal:11434"))

    async def test_ctrl_alt_plus_opens_remote_endpoint_input(self):
        with patch("kitt.router.model_selector.ModelConfigurator.fetch_ollama_models", return_value=["local-model"]):
            await self.ui._execute_command("/setup-models")
            self.pipe.send_text("\x1b\x00")  # Ctrl+Alt++ is Esc followed by Ctrl+@ in standard terminal input.
            await asyncio.sleep(0.05)
        self.assertEqual(self.ui.state.active_overlay, "provider_endpoint")

    async def test_endpoint_input_discovers_models_in_setup_screen(self):
        with patch("kitt.router.model_selector.ModelConfigurator.fetch_ollama_models", return_value=["remote-model"]):
            await self.ui._execute_command("/setup-models")
            self.ui._open_provider_endpoint_overlay()
            self.ui.provider_endpoint_buffer.text = "http://ollama.internal:11434"
            self.ui.provider_endpoint_buffer.validate_and_handle()
            await asyncio.sleep(0.05)
        self.assertEqual(self.ui.state.active_overlay, "model_setup")
        self.assertEqual(self.ui.model_setup_model.base_url_override, "http://ollama.internal:11434")
        self.assertIn("remote-model", self.ui.model_setup_model.models)

    async def test_model_overlay_switches_provider_for_selected_role(self):
        with patch("kitt.router.model_selector.ModelConfigurator.fetch_ollama_models", return_value=["one"]):
            await self.ui._execute_command("/setup-models")
        while self.ui.model_setup_model.selected_provider != "openai":
            await self.ui._move_model_provider(1)
        self.assertIn("gpt-4o", self.ui.model_setup_model.models)
        self.ui.model_setup_model.model_index = self.ui.model_setup_model.models.index("gpt-4o")
        await self.ui._apply_selected_model()
        profile = self.runtime.processor.router.resolve_profile_for_task("code-generation")[1]
        self.assertEqual((profile.backend, profile.model), ("openai", "gpt-4o"))

    async def test_model_overlay_reloads_when_changing_role(self):
        await self.ui._execute_command("/model context openai gpt-4o")
        with patch("kitt.router.model_selector.ModelConfigurator.fetch_ollama_models", return_value=["local-test"]):
            await self.ui._execute_command("/setup-models")
        await self.ui._move_model_role(1)
        self.assertEqual(self.ui.model_setup_model.selected_role, "context")
        self.assertEqual(self.ui.model_setup_model.selected_provider, "openai")
        self.assertEqual(self.ui.model_setup_model.selected_model, "gpt-4o")

    async def test_lmstudio_model_discovery_uses_openai_compatible_endpoint(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"data": [{"id": "local-model"}]}'

        with patch("kitt.ui.app.urllib.request.urlopen", return_value=Response()) as open_url:
            models = await self.ui._models_for_provider("lmstudio", "http://localhost:1234")
        self.assertEqual(models, ["local-model"])
        self.assertEqual(open_url.call_args.args[0].full_url, "http://localhost:1234/v1/models")

    async def test_direct_command_approval_executes_after_allow(self):
        await self.ui._execute_command("/run echo verified")
        self.assertEqual(self.ui.state.active_overlay, "permission")
        self.assertTrue(self.ui.state.pending_approval["direct_tool"])
        await self.ui.resolve_approval(True)
        self.assertIn("verified", self.ui.state.transcript[-1].text)

    async def test_direct_command_denial_does_not_execute(self):
        await self.ui._execute_command("/run echo must-not-run")
        await self.ui.resolve_approval(False)
        self.assertEqual(self.ui.state.status_text, "SYSTEM ONLINE")
        self.assertEqual(self.ui.state.transcript[-1].text, "Command denied.")

    async def test_catalog_commands_have_tui_dispatch_paths(self):
        commands = [
            "/new", "/history", "/thread", "/resume 1", "/conversation", "/fork", "/export-conversation",
            "/doctor", "/add sample.txt", "/drop sample.txt", "/files", "/memory", "/remember keep tests",
            "/clear-memory", "/dream", "/skills", "/setup-skills", "/skill-install", "/skill-remove", "/repomap",
            "/diff", "/undo", "/ask", "/plan", "/code", "/router", "/context-stats", "/stats", "/status",
            "/compact", "/child", "/tasks", "/cancel", "/reasoning", "/approvals", "/autonomy", "/workspace", "/clear", "/help",
            "/child-inspect test_child", "/child-msg test_child hello", "/child-retain test_child", "/child-cancel test_child",
            "/goal-pause test_goal", "/goal-resume test_goal", "/attach test_session", "/detach",
            "/runtime-state", "/artifact test_art",
            "/add-provider meu-ollama ollama http://localhost:11434",
            "/edit-provider meu-ollama http://localhost:11435",
            "/delete-provider meu-ollama",
            "/mode plan",
            "/mouse",
        ]
        tested = set()
        with patch("kitt.router.model_selector.ModelConfigurator.fetch_ollama_models", return_value=["local-test"]):
            commands.append("/setup-models")
            for command in commands:
                handled = await self.ui._execute_command(command)
                self.assertTrue(handled, command)
                tested.add(self.ui.commands.find(command.split(maxsplit=1)[0]).id)
                if self.ui.state.active_overlay:
                    self.ui.close_overlay()
        self.assertTrue(await self.ui._execute_command("/commit test"))
        self.assertEqual(self.ui.state.active_overlay, "permission")
        await self.ui.resolve_approval(False)
        tested.add("commit")
        self.assertTrue(await self.ui._execute_command("/quit"))
        tested.add("quit")
        self.assertEqual(tested, set(self.ui.commands.commands) - {"run", "model"})

    async def test_agents_key_disabled_during_approval(self):
        await self.ui._execute_command("/run echo approval-test")
        self.assertEqual(self.ui.state.active_overlay, "permission")
        with patch.object(self.ui, "open_overlay") as mock_open:
            self.pipe.send_text("a")
            await asyncio.sleep(0.05)
            mock_open.assert_not_called()
        self.assertIsNone(self.ui.state.active_overlay)

    async def test_ctrl_o_toggles_tool_collapse_while_editor_focused(self):
        from kitt.ui.state import TranscriptBlock
        b = TranscriptBlock(id="t1", kind="tool", text="Write(test.py)", collapsed=True, status="done")
        b.metadata["full_output"] = "content"
        self.ui.state.transcript.append(b)

        # Press Ctrl+O (0x0f)
        self.pipe.send_bytes(b"\x0f")
        await asyncio.sleep(0.05)
        self.assertFalse(b.collapsed)

        # Press Ctrl+O again to re-collapse
        self.pipe.send_bytes(b"\x0f")
        await asyncio.sleep(0.05)
        self.assertTrue(b.collapsed)

    async def test_ctrl_c_dismisses_permission_and_unblocks_state(self):
        await self.ui._execute_command("/run echo test")
        self.assertEqual(self.ui.state.active_overlay, "permission")
        self.assertEqual(len(self.ui.state.pending_approvals), 1)

        # Send Ctrl+C (0x03)
        self.pipe.send_bytes(b"\x03")
        await asyncio.sleep(0.05)
        self.assertIsNone(self.ui.state.active_overlay)
        self.assertEqual(len(self.ui.state.pending_approvals), 0)

    async def test_ctrl_x_a_opens_agents_dashboard(self):
        self.pipe.send_bytes(b"\x18a")
        await asyncio.sleep(0.05)
        self.assertEqual(self.ui.state.active_overlay, "agents")
        self.ui.close_overlay()

    async def test_task_and_cancel_commands_work_during_active_execution(self):
        self.ui.state.is_thinking = True

        await self.ui.submit("/task")
        last_block = self.ui.state.transcript[-1]
        self.assertIn("Nenhum agente ou sub-tarefa ativo", last_block.text)

    async def test_export_command_markdown_and_json(self):
        conv = self.runtime.history.get_or_create_active()
        self.runtime.history.repo.save_message(conv["id"], "turn_1", "user", "Hello KITT")
        self.runtime.history.repo.save_message(conv["id"], "turn_1", "assistant", "Hello User")

        await self.ui._execute_command("/export markdown")
        last_block = self.ui.state.transcript[-1]
        self.assertIn("Exportado: kitt_export_", last_block.text)

        await self.ui._execute_command("/export json")
        last_block2 = self.ui.state.transcript[-1]
        self.assertIn("Exportado: kitt_export_", last_block2.text)

    async def test_plan_command_toggle_and_turn_execution(self):
        self.assertFalse(self.ui.state.planning_mode)
        await self.ui._execute_command("/plan")
        self.assertTrue(self.ui.state.planning_mode)
        header_text = [t[1] for t in self.ui._header_text()]
        self.assertTrue(any("PLAN MODE" in t for t in header_text))
        status_text = self.ui._status_text()
        self.assertIn("[PLAN]", status_text)

        await self.ui._execute_command("/plan")
        self.assertFalse(self.ui.state.planning_mode)

    async def test_slash_command_execution_maintains_prompt_focus(self):
        await self.ui.submit("/status")
        self.assertIs(self.ui.application.layout.current_control, self.ui.prompt_control)

        await self.ui.submit("/memory")
        self.assertIs(self.ui.application.layout.current_control, self.ui.prompt_control)


if __name__ == "__main__":
    unittest.main()
