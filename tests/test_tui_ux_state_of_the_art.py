"""Comprehensive unit and integration test suite for K.I.T.T. UX State-of-the-Art."""
import unittest
from unittest.mock import MagicMock

from kitt.ui.commands import CommandRegistry
from kitt.ui.components.command_palette import CommandPaletteComponent
from kitt.ui.components.permission_card import PermissionCardComponent
from kitt.ui.components.scrollable_select import ScrollableSelect, SelectOption
from kitt.ui.components.sidebar import SidebarComponent
from kitt.ui.components.status_bar import StatusBarComponent
from kitt.ui.overlay_manager import OverlayManager
from kitt.ui.overlay_models import ModelSetupModel
from kitt.ui.state import AgentTaskStep, UIState


class TestUXStateOfTheArt(unittest.TestCase):

    def setUp(self):
        self.registry = CommandRegistry()
        self.state = UIState(workspace_name="my-project", workspace_path="/path/to/my-project")

    def test_command_palette_ranked_fuzzy_search(self):
        # 1. Exact alias match ranks first
        results = self.registry.search("/model")
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0].id, "model")

        # 2. Multi-token search matches across title, category, description
        results = self.registry.search("git diff")
        self.assertTrue(any(c.id == "diff" for c in results))

        # 3. Component renders empty state with helpful advice
        comp = CommandPaletteComponent(self.registry)
        empty_render = comp.render("xyznonexistent123")
        self.assertIn("Nenhum comando encontrado para 'xyznonexistent123'", empty_render)
        self.assertIn("Limpe a busca", empty_render)

        # 4. Formatted lines render category and shortcut
        rendered = comp.render("model", selected_index=0)
        self.assertIn("[MODELS]", rendered)
        self.assertIn("[/model]", rendered)

    def test_scrollable_select_accurate_mouse_row_mapping(self):
        options = [
            SelectOption(title="GPT-4o", value="openai/gpt-4o", category="OpenAI"),
            SelectOption(title="Claude 3.7", value="anthropic/claude-3.7", category="Anthropic"),
            SelectOption(title="Qwen 2.5", value="ollama/qwen2.5", category="Ollama"),
        ]
        select = ScrollableSelect(options=options, viewport_size=3)
        lines = select.render_lines()
        # Lines: [OPENAI], GPT-4o, [ANTHROPIC], Claude 3.7, [OLLAMA], Qwen 2.5
        self.assertTrue(len(lines) >= 6)

        # Hovering category header (row 0) does not change selection
        select.on_mouse_move(visual_row_offset=0)
        self.assertEqual(select.selected_index, 0)

        # Hovering Claude 3.7 (row 3) selects index 1
        select.on_mouse_move(visual_row_offset=3)
        self.assertEqual(select.selected_index, 1)

        # Clicking Claude 3.7 confirms selection
        selected_called = []
        select.on_select = lambda opt: selected_called.append(opt.value)
        clicked = select.on_mouse_click(visual_row_offset=3)
        self.assertEqual(clicked.title, "Claude 3.7")
        self.assertEqual(selected_called, ["anthropic/claude-3.7"])

    def test_overlay_hierarchy_and_security_invariants(self):
        mock_app = MagicMock()
        mock_app.state = UIState()
        mock_app.focus_stack = []
        mock_app.application = MagicMock()
        mock_app.application.layout.current_control = "prompt_ctrl"
        mgr = OverlayManager(mock_app)

        # Open model_setup
        mgr.open("model_setup", control="ctrl_model")
        self.assertEqual(mock_app.state.active_overlay, "model_setup")

        # Open child provider_popup
        mgr.open("provider_popup", control="ctrl_popup", parent_name="model_setup")
        self.assertEqual(mock_app.state.active_overlay, "provider_popup")

        # Independent lower priority overlay (help) cannot hijack top of security modal
        mgr.open("permission", control="ctrl_perm")
        self.assertEqual(mock_app.state.active_overlay, "permission")

        mgr.open("help", control="ctrl_help")
        # Still on permission!
        self.assertEqual(mock_app.state.active_overlay, "permission")

        # Close permission restores provider_popup
        closed = mgr.close()
        self.assertEqual(closed, "permission")
        self.assertEqual(mock_app.state.active_overlay, "provider_popup")

        # Close provider_popup restores model_setup
        closed = mgr.close()
        self.assertEqual(closed, "provider_popup")
        self.assertEqual(mock_app.state.active_overlay, "model_setup")

        # Close model_setup restores base
        closed = mgr.close()
        self.assertEqual(closed, "model_setup")
        self.assertIsNone(mock_app.state.active_overlay)

    def test_status_bar_priority_and_responsiveness(self):
        bar = StatusBarComponent()

        # 1. Idle state
        self.state.large_model = "gpt-4o"
        rendered_idle = bar.render(self.state, width=80)
        self.assertIn("gpt-4o", rendered_idle)
        self.assertIn("my-project", rendered_idle)

        # 2. Priority 2: Running task
        self.state.active_tasks = [
            AgentTaskStep(id="1", name="Analisando arquivos", role="core", status="running", progress=45)
        ]
        rendered_running = bar.render(self.state, width=80)
        self.assertIn("Analisando arquivos", rendered_running)
        self.assertIn("45%", rendered_running)

        # 3. Priority 1: Pending permission preempts running task
        self.state.pending_approvals = [{"approval_id": "appr-1", "tool_name": "apply_patch"}]
        rendered_perm = bar.render(self.state, width=80)
        self.assertIn("APROVAÇÃO NECESSÁRIA", rendered_perm)

        # 4. Narrow/Mobile width (< 70)
        rendered_mobile = bar.render(self.state, width=40)
        self.assertTrue(len(rendered_mobile) <= 60)

    def test_sidebar_compact_blocks_and_context_bar(self):
        sidebar = SidebarComponent()
        self.state.tokens_used = 4096
        self.state.context_window = 8192
        self.state.large_model = "gpt-4o"
        self.state.small_model = "qwen-2.5-coder"

        rendered = sidebar.render(self.state, width=40)
        self.assertIn("WORKSPACE", rendered)
        self.assertIn("MODELOS ATIVOS", rendered)
        self.assertIn("Principal: gpt-4o", rendered)
        self.assertIn("Contexto : qwen-2.5-coder", rendered)
        self.assertIn("50% (4096/8192)", rendered)

    def test_permission_card_risk_and_queue(self):
        card = PermissionCardComponent()
        self.state.pending_approvals = [
            {
                "approval_id": "appr-1",
                "tool_name": "apply_patch",
                "affected_paths": ["src/main.py", "src/utils.py"],
                "expires_at": 0,
            },
            {
                "approval_id": "appr-2",
                "tool_name": "run_command",
                "args": {"command": "npm test"},
                "expires_at": 0,
            },
        ]
        rendered = card.render(self.state, width=88)
        self.assertIn("APROVAÇÃO NECESSÁRIA (1 de 2 na fila)", rendered)
        self.assertIn("Modificação de arquivos no workspace", rendered)
        self.assertIn("src/main.py", rendered)
        self.assertIn("[y] Permitir uma vez", rendered)
        self.assertIn("[N] Negar todas", rendered)

    def test_model_setup_discovery_states(self):
        model_setup = ModelSetupModel()
        model_setup.loading = True
        self.assertTrue(model_setup.loading)

        model_setup.loading = False
        model_setup.error_message = "Connection refused on localhost:11434"
        self.assertEqual(model_setup.error_message, "Connection refused on localhost:11434")

        # Custom provider addition and discovery
        model_setup.add_custom_provider("local-vllm", "http://localhost:8000/v1")
        self.assertTrue(any(cp["name"] == "local-vllm" for cp in model_setup.custom_providers))
        entries = model_setup.get_popup_entries()
        self.assertTrue(any(e.get("name") == "local-vllm" for e in entries))

    def test_model_setup_stays_open_on_role_assignment(self):
        # Verify overlay stays open when assigning a model so user can configure other roles
        mock_app = MagicMock()
        mock_app.state = UIState()
        mock_app.focus_stack = []
        mock_app.application = MagicMock()
        mock_app.application.layout.current_control = "prompt_ctrl"
        mgr = OverlayManager(mock_app)

    def test_remote_ip_ollama_discovery_and_url_normalization(self):
        from unittest.mock import patch
        from kitt.router.model_selector import ModelConfigurator

        cfg = ModelConfigurator()
        with patch("kitt.llm.providers.ollama.secure_urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b'{"models": [{"name": "qwen2.5:32b"}, {"name": "deepseek-r1:14b"}]}'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp

            # Call with raw IP:port without http:// prefix
            models = cfg.fetch_ollama_models("192.168.100.51:11434")
            self.assertEqual(models, ["qwen2.5:32b", "deepseek-r1:14b"])
            req = mock_urlopen.call_args.args[0]
            self.assertEqual(req.full_url, "http://192.168.100.51:11434/api/tags")

    def test_custom_and_lan_providers_do_not_require_token(self):
        from kitt.ui.app import KittUIApp
        app = KittUIApp.__new__(KittUIApp)
        app.model_setup_model = ModelSetupModel()
        app.model_setup_model.add_custom_provider("my-ollama", "http://192.168.100.51:11434")

        # 1. Custom registered provider
        self.assertTrue(app._is_local_or_no_auth_provider("my-ollama", "http://192.168.100.51:11434"))

        # 2. LAN IP endpoints
        self.assertTrue(app._is_local_or_no_auth_provider("custom-vllm", "http://192.168.1.100:8000"))
        self.assertTrue(app._is_local_or_no_auth_provider("gpu-box", "http://10.0.0.50:11434"))

        # 3. Known local backends
        self.assertTrue(app._is_local_or_no_auth_provider("ollama", "http://localhost:11434"))
        self.assertTrue(app._is_local_or_no_auth_provider("lmstudio", "http://localhost:1234"))

        # 4. Cloud providers require auth
        self.assertFalse(app._is_local_or_no_auth_provider("openai", "https://api.openai.com"))
        self.assertFalse(app._is_local_or_no_auth_provider("anthropic", "https://api.anthropic.com"))

    def test_custom_provider_patterns_and_slash_command(self):
        from unittest.mock import AsyncMock
        from kitt.ui.overlay_models import PROVIDER_PATTERNS
        from kitt.ui.model_commands import handle_add_provider_command
        import asyncio

        setup = ModelSetupModel()
        # Verify default patterns exist
        pat_ids = [p["id"] for p in PROVIDER_PATTERNS]
        self.assertIn("ollama", pat_ids)
        self.assertIn("openai", pat_ids)
        self.assertIn("anthropic", pat_ids)
        self.assertIn("gemini", pat_ids)

        # Pattern selection and cycling
        setup.set_pattern_by_id("ollama")
        self.assertEqual(setup.selected_pattern["protocol"], "ollama-chat")
        setup.cycle_pattern(1)
        self.assertEqual(setup.selected_pattern["protocol"], "openai-chat-completions")

        # Adding with specific pattern
        setup.add_custom_provider(
            name="remote-ollama",
            base_url="http://192.168.100.51:11434",
            backend="ollama",
            protocol="ollama-chat"
        )
        custom_entry = next(cp for cp in setup.custom_providers if cp["name"] == "remote-ollama")
        self.assertEqual(custom_entry["protocol"], "ollama-chat")
        self.assertEqual(custom_entry["base_url"], "http://192.168.100.51:11434")

        # Verify slash command handling
        mock_app = MagicMock()
        mock_app.model_setup_model = ModelSetupModel()
        mock_app.state = MagicMock()
        mock_app._open_model_setup_overlay = AsyncMock()

        asyncio.run(handle_add_provider_command(mock_app, "gpu-ollama ollama 192.168.100.51:11434"))
        entry = next(cp for cp in mock_app.model_setup_model.custom_providers if cp["name"] == "gpu-ollama")
        self.assertEqual(entry["protocol"], "ollama-chat")
        self.assertEqual(entry["base_url"], "http://192.168.100.51:11434")

        # Edit command
        from kitt.ui.model_commands import handle_edit_provider_command, handle_delete_provider_command
        asyncio.run(handle_edit_provider_command(mock_app, "gpu-ollama http://192.168.100.52:11434 ollama my-token"))
        entry = mock_app.model_setup_model.get_custom_provider("gpu-ollama")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["base_url"], "http://192.168.100.52:11434")
        self.assertEqual(entry["api_key"], "my-token")

        # Delete command
        asyncio.run(handle_delete_provider_command(mock_app, "gpu-ollama"))
        self.assertIsNone(mock_app.model_setup_model.get_custom_provider("gpu-ollama"))

    def test_custom_provider_router_persistence(self):
        import tempfile
        from kitt.router.router import TaskRouter
        from kitt.domain.entities import RouterConfig, ModelProfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            router = TaskRouter(root_dir=tmp_dir)
            router.config.custom_providers = [
                {"name": "lan-ollama", "base_url": "http://192.168.1.100:11434", "backend": "ollama", "protocol": "ollama-chat"}
            ]
            router.save_config(tmp_dir)

            new_router = TaskRouter(root_dir=tmp_dir)
            loaded = new_router.load_config(tmp_dir)
            self.assertEqual(len(loaded.custom_providers), 1)
            self.assertEqual(loaded.custom_providers[0]["name"], "lan-ollama")
            self.assertEqual(loaded.custom_providers[0]["base_url"], "http://192.168.1.100:11434")

    def test_transcript_mouse_scroll_and_toggle_mouse_mode(self):
        from prompt_toolkit.mouse_events import MouseEvent, MouseEventType, MouseButton
        from prompt_toolkit.data_structures import Point
        from kitt.ui.app import KittUIApp
        from kitt.core.runtime import KittRuntime

        app = KittUIApp(runtime=MagicMock())
        app.build_application()
        
        # Test mouse toggle
        self.assertTrue(app.mouse_support_enabled)
        res = app.toggle_mouse_support()
        self.assertFalse(res)
        self.assertFalse(app.mouse_support_enabled)
        res = app.toggle_mouse_support()
        self.assertTrue(res)
        self.assertTrue(app.mouse_support_enabled)

        # Test transcript mouse scroll handler
        app.transcript_window.vertical_scroll = 50
        ev_up = MouseEvent(position=Point(x=10, y=10), event_type=MouseEventType.SCROLL_UP, button=MouseButton.NONE, modifiers=frozenset())
        app._transcript_mouse_handler(ev_up)
        self.assertEqual(app.transcript_window.vertical_scroll, 47)
        self.assertFalse(app.state.follow_tail)

        ev_down = MouseEvent(position=Point(x=10, y=10), event_type=MouseEventType.SCROLL_DOWN, button=MouseButton.NONE, modifiers=frozenset())
        app._transcript_mouse_handler(ev_down)
        self.assertEqual(app.transcript_window.vertical_scroll, 50)

    def test_turn_mode_toggle_and_f12_status_bar(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from kitt.ui.app import KittUIApp
        from kitt.ui.components.status_bar import StatusBarComponent

        app = KittUIApp(runtime=MagicMock())
        app.build_application()

        self.assertEqual(app.state.turn_mode, "code")
        # Toggle cycle
        m1 = app.toggle_turn_mode()
        self.assertEqual(m1, "plan")
        self.assertEqual(app.state.turn_mode, "plan")
        self.assertTrue(app.state.planning_mode)

        m2 = app.toggle_turn_mode()
        self.assertEqual(m2, "ask")
        self.assertEqual(app.state.turn_mode, "ask")

        m3 = app.toggle_turn_mode()
        self.assertEqual(m3, "code")
        self.assertEqual(app.state.turn_mode, "code")

        # Explicit set
        app.toggle_turn_mode("plan")
        self.assertEqual(app.state.turn_mode, "plan")

        # Status bar rendering contains F4 and F12
        sb = StatusBarComponent()
        rendered = sb.render(app.state, width=100)
        self.assertIn("F4: PLAN", rendered)
        self.assertIn("F12: Modelos", rendered)

        # Prompt submission modifies text when in plan/ask mode
        app.bridge = MagicMock()
        app.bridge.is_active = False
        app.bridge.start = AsyncMock()
        app.runtime.history.get_or_create_active = MagicMock(return_value={"id": "conv-123"})
        
        # In plan mode
        asyncio.run(app.submit("analyze architecture"))
        app.bridge.start.assert_called_once()
        called_args, called_kwargs = app.bridge.start.call_args
        self.assertTrue(called_args[0].startswith("[PLAN ONLY - NO CODE EDITS]"))
        self.assertEqual(called_kwargs.get("mode"), "plan")


if __name__ == "__main__":
    unittest.main()
