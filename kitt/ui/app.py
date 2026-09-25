from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import json
import os
import re
import shlex
import urllib.request
import uuid
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional, Tuple

from importlib.metadata import PackageNotFoundError, version as package_version
from kitt.core.turn_events import ApprovalRequired
from kitt.ui.commands import CommandRegistry
from kitt.ui.event_bridge import TurnEventBridge
from kitt.ui.git import read_git_branch_name
from kitt.ui.keymap import KeyMap
from kitt.ui.layout import LayoutDimensions, build_root_container
from kitt.ui.overlay_models import DiffViewerModel, ModelSetupModel, OverlayFrame, SessionPickerModel, TimelineModel
from kitt.ui.reducer import reduce_ui_event
from kitt.ui.state import UIState, safe_text
from kitt.ui.theme import DEFAULT_THEME
from kitt.ui.model_commands import (
    handle_model_command, handle_setup_models_command, handle_add_provider_command,
    handle_edit_provider_command, handle_delete_provider_command, handle_local_limits_command, parse_model_command
)
from kitt.ui.session_commands import (
    handle_resume_command, handle_fork_command, handle_export_command,
    handle_compact_command, handle_stats_command, handle_gain_command,
    handle_status_command
)
from kitt.ui.skill_commands import (
    handle_setup_skills_command, handle_skill_install_command, handle_skill_remove_command,
    handle_remember_command, handle_clear_memory_command, handle_doctor_command
)
from kitt.ui.dream_commands import handle_dream_command, handle_memory_extended_command
from kitt.ui.overlay_manager import OverlayManager
from kitt.ui import command_dispatcher as _command_dispatcher
from kitt.ui import model_service as _model_service
from kitt.ui import provider_flow as _provider_flow
from kitt.ui import runtime_actions as _runtime_actions


def _agent_version() -> str:
    try:
        return package_version("kitt-agent-cli")
    except PackageNotFoundError:
        return "dev"


class KittUIApp:
    """Single-owner full-screen prompt_toolkit application."""

    def __init__(self, runtime, mode: str = "auto", *, input=None, output=None, no_animation: bool = False):
        self.runtime = runtime
        self.mode = mode.lower()
        self.input = input
        self.output = output
        self.no_animation = no_animation
        root = Path(runtime.canonical_root)
        self.state = UIState(
            workspace_name=root.name or str(root),
            workspace_path=str(root),
            current_branch=read_git_branch_name(root),
        )
        self.session_picker_model = SessionPickerModel(runtime)
        self.timeline_model = TimelineModel(runtime)
        self.diff_model = DiffViewerModel(str(root))
        self.model_setup_model = ModelSetupModel()
        self.mouse_support_enabled: bool = True
        self.editing_provider_name: Optional[str] = None
        self.scrollable_windows: dict[str, Any] = {}
        self.state.mouse_enabled = True

        self._init_models_from_runtime()
        self.commands = CommandRegistry()
        self.keymap = KeyMap()
        self.explicit_files: set[str] = set()
        self.application = None
        self.bridge = None
        self._animation_task = None
        self._blocking_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="kitt-ui-blocking")
        self._shutdown = False
        self.palette_index = 0
        self.focus_stack: list[OverlayFrame] = []
        self.overlay_manager = OverlayManager(self)
        self._build_controls()

    async def _run_blocking(self, func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        call = functools.partial(func, *args, **kwargs)
        return await loop.run_in_executor(self._blocking_executor, call)

    async def _ensure_daemon_management(self) -> bool:
        bridge = getattr(self, "bridge", None)
        config = getattr(self.runtime, "config", None)
        if (
            bridge is None
            or config is None
            or not getattr(config, "daemon_enabled", False)
            or not getattr(config, "history_enabled", False)
            or not getattr(config, "persistence_enabled", False)
        ):
            return False
        conversation = self.runtime.history.get_or_create_active()
        return await bridge.ensure_daemon(conversation["id"])

    def _init_models_from_runtime(self) -> None:
        try:
            router = getattr(self.runtime.processor, "router", None)
            if router and hasattr(router, "config") and router.config:
                _, context = router.resolve_profile_for_task("context-gather")
                _, execute = router.resolve_profile_for_task("code-generation")
                self.state.small_model = context.model
                self.state.large_model = execute.model
                saved_custom = getattr(router.config, "custom_providers", [])
                if saved_custom and hasattr(self, "model_setup_model"):
                    from kitt.llm.endpoint_security import is_reserved_provider_id
                    for cp in saved_custom:
                        if is_reserved_provider_id(cp.get("name", "")):
                            continue
                        self.model_setup_model.add_custom_provider(
                            name=cp.get("name", ""),
                            base_url=cp.get("base_url", ""),
                            backend=cp.get("backend", "openai"),
                            protocol=cp.get("protocol", "openai-chat-completions"),
                            api_key=cp.get("api_key", ""),
                        )
                        if hasattr(self.runtime.processor, "registry"):
                            self.runtime.processor.registry.register_custom_provider(
                                provider_id=cp.get("name", ""),
                                name=cp.get("name", ""),
                                protocol=cp.get("protocol", "openai-chat-completions"),
                                base_url=cp.get("base_url", ""),
                            )
        except Exception:
            pass

        # Autonomous detection of KITT Reverse Proxy
        try:
            from kitt.llm.health import ProviderHealthChecker
            proxy_url = os.environ.get("KITT_REVERSE_PROXY_URL", "http://127.0.0.1:3000")
            online, _ = ProviderHealthChecker.check_kitt_reverse_proxy(proxy_url, timeout=0.8)
            if online:
                if hasattr(self, "model_setup_model"):
                    if "kitt-reverse-proxy" not in self.model_setup_model.favorite_providers:
                        self.model_setup_model.favorite_providers.insert(0, "kitt-reverse-proxy")
                if hasattr(self.runtime.processor, "registry"):
                    from kitt.llm.catalog import ProviderDescriptor
                    self.runtime.processor.registry.register_provider(ProviderDescriptor(
                        id="kitt-reverse-proxy",
                        name="KITT Reverse Proxy",
                        protocol="kitt-reverse-proxy",
                        base_url=proxy_url,
                        env_vars=("KITT_REVERSE_PROXY_API_KEY", "KITT_REVERSE_PROXY_URL"),
                        auth_methods=("api_key",),
                        local=True,
                    ))
        except Exception:
            pass

    @property
    def dimensions(self) -> LayoutDimensions:
        return LayoutDimensions(self.state.width, self.state.height)

    def _build_controls(self) -> None:
        from kitt.ui.controls import build_controls
        build_controls(self)

    def build_application(self):
        from prompt_toolkit.application import Application
        from prompt_toolkit.cursor_shapes import CursorShape
        from prompt_toolkit.filters import Condition
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.output import DummyOutput
        from prompt_toolkit.output.defaults import create_output

        output = self.output
        if output is None:
            try:
                output = create_output()
            except Exception:
                output = DummyOutput()

        root = build_root_container(self)
        self.application = Application(
            layout=Layout(root, focused_element=self.prompt_control),
            key_bindings=self._key_bindings(),
            style=DEFAULT_THEME.prompt_toolkit_style(),
            full_screen=True,
            cursor=CursorShape.BLINKING_BEAM,
            mouse_support=Condition(lambda: getattr(self, "mouse_support_enabled", False)),
            refresh_interval=None,
            min_redraw_interval=1 / 30,
            input=self.input,
            output=output,
            before_render=self._before_render,
        )
        self.bridge = TurnEventBridge(self.runtime, self._on_event, self.application.invalidate)
        return self.application

    def _before_render(self, app) -> None:
        size = app.output.get_size()
        self.state.width, self.state.height = size.columns, size.rows

    def _accept_prompt(self, buffer) -> bool:
        text = buffer.text
        if text.endswith("\n"):
            text = text[:-1]
        if text.strip() and not self.state.is_thinking and not (self.bridge and self.bridge.is_active):
            buffer.append_to_history()
            buffer.reset()
            asyncio.get_running_loop().create_task(self.submit(text.strip()))
        return True

    def _accept_provider_endpoint(self, buffer) -> bool:
        endpoint = buffer.text.strip()
        if endpoint and not endpoint.startswith(("http://", "https://")):
            endpoint = f"http://{endpoint}"
        if endpoint:
            buffer.reset()
            asyncio.get_running_loop().create_task(self._submit_provider_endpoint(endpoint))
        return True

    async def submit(self, text: str) -> None:
        if text in {"/quit", "/exit"}:
            self.request_exit()
            return
        if text.startswith("/") and await self._execute_command(text):
            if self.application:
                if not self.state.active_overlay:
                    try:
                        self.application.layout.focus(self.prompt_control)
                    except Exception:
                        pass
                self.application.invalidate()
            return
        if self.state.is_thinking or (self.bridge and self.bridge.is_active):
            return
        conversation = self.runtime.history.get_or_create_active()
        mode = "plan" if (self.state.planning_mode or self.state.turn_mode == "plan") else ("ask" if self.state.turn_mode == "ask" else "auto")
        if self.state.turn_mode == "plan" and not text.startswith("[PLAN"):
            text = f"[PLAN ONLY - NO CODE EDITS]: {text}"
        elif self.state.turn_mode == "ask" and not text.startswith("[QUESTION"):
            text = f"[QUESTION ONLY - NO CODE EDITS]: {text}"

        inline_files = set(re.findall(r'@([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+)', text))
        if inline_files:
            self.explicit_files.update(inline_files)
        combined_explicit = set(self.explicit_files)
        try:
            await self.bridge.start(text, conversation["id"], explicit_files=combined_explicit, no_history=not self.runtime.config.history_enabled, mode=mode)
        except Exception as err:
            self.state.is_thinking = False
            self.state.add_toast(f"Turn Error: {err}")

    def _on_event(self, event) -> None:
        from kitt.core.turn_events import TurnCompleted, TurnFailed, TurnCancelled, TurnBlocked
        reduce_ui_event(self.state, event)
        if isinstance(event, ApprovalRequired) and self.application:
            self.approval_menu_index = 0
            self.open_overlay("permission", self.permission_control)
        elif isinstance(event, (TurnCompleted, TurnFailed, TurnCancelled, TurnBlocked)) and self.application:
            if not self.state.active_overlay:
                try:
                    self.application.layout.focus(self.prompt_control)
                except Exception:
                    pass
        if self.state.follow_tail and hasattr(self, "transcript_window"):
            self.transcript_window.vertical_scroll = 10**9
        if self.application:
            self.application.invalidate()

    async def _execute_command(self, *args, **kwargs):
        return await _command_dispatcher._execute_command(self, *args, **kwargs)

    async def _export_conversation(self, fmt: str) -> None:
        conv = self.runtime.history.get_active_read_only()
        if not conv:
            self._show_result("Nenhuma conversa ativa.")
            return
        msgs = await self._run_blocking(
            self.runtime.history.repo.get_messages_for_conversation, conv["id"]
        )
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        if fmt == "json":
            content = json.dumps(msgs, indent=2, ensure_ascii=False)
            filename = f"kitt_export_{timestamp}.json"
        else:
            lines = ["# K.I.T.T. Conversation Export\n"]
            for m in msgs:
                role = "**User**" if m["role"] == "user" else "**K.I.T.T.**"
                lines.append(f"\n{role}:\n\n{m['content']}\n\n---")
            content = "\n".join(lines)
            filename = f"kitt_export_{timestamp}.md"
        out_path = Path(self.state.workspace_path) / filename
        out_path.write_text(content, encoding="utf-8")
        self._show_result(f"Exportado: {filename}")

    def _parse_model_command(self, *args, **kwargs):
        return _model_service._parse_model_command(self, *args, **kwargs)

    def _role_tasks(self, *args, **kwargs):
        return _model_service._role_tasks(self, *args, **kwargs)

    def _model_for_role(self, *args, **kwargs):
        return _model_service._model_for_role(self, *args, **kwargs)

    def _profile_for_role(self, *args, **kwargs):
        return _model_service._profile_for_role(self, *args, **kwargs)

    @staticmethod
    def _provider_defaults(provider: str) -> tuple[str, str]:
        defaults = {
            "ollama": (os.environ.get("OLLAMA_HOST", "http://localhost:11434"), ""),
            "lmstudio": (os.environ.get("LMSTUDIO_HOST", "http://localhost:1234"), ""),
            "openai": ("https://api.openai.com", os.environ.get("OPENAI_API_KEY", "")),
            "anthropic": ("https://api.anthropic.com", os.environ.get("ANTHROPIC_API_KEY", "")),
            "gemini": ("https://generativelanguage.googleapis.com", os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")),
            "deepseek": ("https://api.deepseek.com", os.environ.get("DEEPSEEK_API_KEY", "")),
            "groq": ("https://api.groq.com/openai", os.environ.get("GROQ_API_KEY", "")),
            "together": ("https://api.together.xyz", os.environ.get("TOGETHER_API_KEY", "")),
            "mistral": ("https://api.mistral.ai", os.environ.get("MISTRAL_API_KEY", "")),
            "openrouter": ("https://openrouter.ai/api", os.environ.get("OPENROUTER_API_KEY", "")),
            "xai": ("https://api.xai.com", os.environ.get("XAI_API_KEY", "")),
            "fireworks": ("https://api.fireworks.ai/inference", os.environ.get("FIREWORKS_API_KEY", "")),
            "cohere": ("https://api.cohere.com", os.environ.get("COHERE_API_KEY", "")),
            "azure": (os.environ.get("AZURE_OPENAI_ENDPOINT", "https://your-resource.openai.azure.com"), os.environ.get("AZURE_OPENAI_API_KEY", "")),
            "antigravity": ("https://api.antigravity.dev", os.environ.get("ANTIGRAVITY_API_KEY", "")),
            "kitt-reverse-proxy": (os.environ.get("KITT_REVERSE_PROXY_URL", "http://127.0.0.1:3000"), ""),
            "kitt-proxy": (os.environ.get("KITT_REVERSE_PROXY_URL", "http://127.0.0.1:3000"), ""),
        }
        if provider in defaults:
            return defaults[provider]
        p_lower = (provider or "").strip().lower()
        if "ollama" in p_lower:
            return (os.environ.get("OLLAMA_HOST", "http://localhost:11434"), "")
        if "lmstudio" in p_lower:
            return (os.environ.get("LMSTUDIO_HOST", "http://localhost:1234"), "")
        try:
            from kitt.llm.catalog import ProviderCatalogService
            cat = ProviderCatalogService()
            cat_p = cat.provider(provider)
            if cat_p and cat_p.base_url:
                env_val = os.environ.get(cat_p.env_vars[0], "") if cat_p.env_vars else ""
                return (cat_p.base_url, env_val)
        except Exception:
            pass
        env_key = os.environ.get(f"{provider.upper().replace('-', '_').replace(' ', '_')}_API_KEY", "")
        env_host = os.environ.get(f"{provider.upper().replace('-', '_').replace(' ', '_')}_HOST", "http://localhost:11434" if "ollama" in p_lower else "http://localhost:8000/v1")
        return (env_host, env_key)

    async def _set_model_role(self, *args, **kwargs):
        return await _model_service._set_model_role(self, *args, **kwargs)

    async def _toggle_role_local_limits(self, *args, **kwargs):
        return await _model_service._toggle_role_local_limits(self, *args, **kwargs)

    async def _models_for_provider(self, *args, **kwargs):
        return await _model_service._models_for_provider(self, *args, **kwargs)

    async def _prepare_model_setup(self, *args, **kwargs):
        return await _model_service._prepare_model_setup(self, *args, **kwargs)

    def _model_setup_search_changed(self, *args, **kwargs):
        return _model_service._model_setup_search_changed(self, *args, **kwargs)

    async def _open_model_setup_overlay(self, *args, **kwargs):
        return await _model_service._open_model_setup_overlay(self, *args, **kwargs)

    def _transcript_mouse_handler(self, mouse_event) -> Any:
        from prompt_toolkit.mouse_events import MouseEventType
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self._scroll_transcript(-3)
            return None
        elif mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self._scroll_transcript(3)
            return None
        return NotImplemented

    def _prompt_mouse_handler(self, mouse_event) -> Any:
        from prompt_toolkit.mouse_events import MouseEventType
        if mouse_event.event_type in {MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN}:
            return self._transcript_mouse_handler(mouse_event)
        return self._prompt_default_mouse_handler(mouse_event)

    def _scroll_transcript(self, delta: int) -> None:
        if not hasattr(self, "transcript_window"):
            return
        window = self.transcript_window
        if delta < 0:
            self.state.follow_tail = False
            window.vertical_scroll = max(0, window.vertical_scroll + delta)
        else:
            info = getattr(window, "render_info", None)
            if info is not None and info.bottom_visible:
                self.state.follow_tail = True
                self.state.unseen_output = False
                window.vertical_scroll = 10**9
            else:
                window.vertical_scroll += delta
        if self.application:
            self.application.invalidate()

    def _permission_mouse_handler(self, mouse_event) -> Any:
        from prompt_toolkit.mouse_events import MouseEventType
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.permission_window.vertical_scroll = max(0, self.permission_window.vertical_scroll - 3)
        elif mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.permission_window.vertical_scroll += 3
        else:
            return NotImplemented
        if self.application:
            self.application.invalidate()
        return None

    def toggle_mouse_support(self) -> bool:
        self.mouse_support_enabled = not getattr(self, "mouse_support_enabled", True)
        self.state.mouse_enabled = self.mouse_support_enabled
        if self.application and hasattr(self.application, "output"):
            try:
                if self.mouse_support_enabled:
                    self.application.output.enable_mouse_support()
                else:
                    self.application.output.disable_mouse_support()
            except Exception:
                pass
        msg = "Mouse TUI ativado (Scroll Interativo)" if self.mouse_support_enabled else "Mouse Terminal Nativo (Seleção/Cópia de Texto Habilitada)"
        self.state.add_toast(msg)
        if self.application:
            self.application.invalidate()
        return self.mouse_support_enabled

    def toggle_turn_mode(self, target_mode: str | None = None) -> str:
        modes = ["code", "plan", "ask"]
        if target_mode and target_mode.lower() in modes:
            self.state.turn_mode = target_mode.lower()
        else:
            curr_idx = modes.index(self.state.turn_mode) if self.state.turn_mode in modes else 0
            self.state.turn_mode = modes[(curr_idx + 1) % len(modes)]

        self.state.planning_mode = (self.state.turn_mode == "plan")
        mode_descs = {
            "code": "Modo [CODE] ativo: edição e execução de ferramentas habilitadas",
            "plan": "Modo [PLAN] ativo: análise e planejamento (sem escrita de código)",
            "ask": "Modo [ASK] ativo: pergunta e dúvidas (sem chamadas de ferramentas)",
        }
        self.state.add_toast(mode_descs.get(self.state.turn_mode, f"Modo: {self.state.turn_mode.upper()}"), persistent=False)
        if self.application:
            self.application.invalidate()
        return self.state.turn_mode

    def _model_setup_mouse_handler(self, mouse_event) -> Any:
        from prompt_toolkit.mouse_events import MouseEventType
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.model_setup_model.move_model(-1)
            if self.application:
                self.application.invalidate()
            return None
        elif mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.model_setup_model.move_model(1)
            if self.application:
                self.application.invalidate()
            return None
        elif mouse_event.event_type == MouseEventType.MOUSE_MOVE:
            self.model_setup_model.handle_mouse_hover(mouse_event.position.y)
            if self.application:
                self.application.invalidate()
            return None
        elif mouse_event.event_type == MouseEventType.MOUSE_UP:
            self.model_setup_model.handle_mouse_hover(mouse_event.position.y)
            asyncio.create_task(self._apply_selected_model())
            return None
        return NotImplemented

    def _provider_popup_mouse_handler(self, mouse_event) -> Any:
        from prompt_toolkit.mouse_events import MouseEventType
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.model_setup_model.move_popup_selection(-1)
            if self.application:
                self.application.invalidate()
            return None
        elif mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.model_setup_model.move_popup_selection(1)
            if self.application:
                self.application.invalidate()
            return None
        elif mouse_event.event_type == MouseEventType.MOUSE_MOVE:
            self.model_setup_model.handle_popup_mouse_hover(mouse_event.position.y)
            if self.application:
                self.application.invalidate()
            return None
        elif mouse_event.event_type == MouseEventType.MOUSE_UP:
            self.model_setup_model.handle_popup_mouse_hover(mouse_event.position.y)
            entry = self.model_setup_model.get_selected_popup_entry()
            if entry:
                if entry["kind"] == "action":
                    self._select_popup_action(entry)
                elif entry["kind"] == "provider":
                    self.close_overlay()
                    asyncio.create_task(self._select_provider_from_popup(entry["name"]))
            return None
        return NotImplemented

    def _select_popup_action(self, *args, **kwargs):
        return _provider_flow._select_popup_action(self, *args, **kwargs)

    def _open_provider_popup_overlay(self, *args, **kwargs):
        return _provider_flow._open_provider_popup_overlay(self, *args, **kwargs)

    def _provider_popup_text(self, *args, **kwargs):
        return _provider_flow._provider_popup_text(self, *args, **kwargs)

    async def _persist_custom_providers(self, *args, **kwargs):
        return await _provider_flow._persist_custom_providers(self, *args, **kwargs)

    def _open_add_provider_overlay(self, *args, **kwargs):
        return _provider_flow._open_add_provider_overlay(self, *args, **kwargs)

    def _open_edit_provider_overlay(self, *args, **kwargs):
        return _provider_flow._open_edit_provider_overlay(self, *args, **kwargs)

    async def _delete_custom_provider(self, *args, **kwargs):
        return await _provider_flow._delete_custom_provider(self, *args, **kwargs)

    def _add_provider_help_text(self, *args, **kwargs):
        return _provider_flow._add_provider_help_text(self, *args, **kwargs)

    def _accept_add_provider(self, *args, **kwargs):
        return _provider_flow._accept_add_provider(self, *args, **kwargs)

    async def _finish_add_provider(self, *args, **kwargs):
        return await _provider_flow._finish_add_provider(self, *args, **kwargs)

    def _open_provider_endpoint_overlay(self, *args, **kwargs):
        return _provider_flow._open_provider_endpoint_overlay(self, *args, **kwargs)

    @staticmethod
    def _provider_endpoint_text():
        return "Informe a URL do endpoint remoto (ex: http://192.168.1.50:11434):\n[Enter] Descobrir Modelos  |  [Esc] Cancelar\n"

    async def _submit_provider_endpoint(self, *args, **kwargs):
        return await _provider_flow._submit_provider_endpoint(self, *args, **kwargs)

    def _auth_login_help_text(self, *args, **kwargs):
        return _provider_flow._auth_login_help_text(self, *args, **kwargs)

    async def _start_oauth_flow(self, *args, **kwargs):
        return await _provider_flow._start_oauth_flow(self, *args, **kwargs)

    def _accept_model_setup_search(self, *args, **kwargs):
        return _provider_flow._accept_model_setup_search(self, *args, **kwargs)

    def _open_auth_login_overlay(self, *args, **kwargs):
        return _provider_flow._open_auth_login_overlay(self, *args, **kwargs)

    def _accept_auth_login(self, *args, **kwargs):
        return _provider_flow._accept_auth_login(self, *args, **kwargs)

    async def _apply_pending_model(self, *args, **kwargs):
        return await _provider_flow._apply_pending_model(self, *args, **kwargs)

    def _agents_text(self) -> str:
        from kitt.ui.components.agents_dashboard import AgentsDashboardComponent
        return AgentsDashboardComponent().render(self.state, max(40, self.state.width - 16))

    def _live_agents_text(self) -> str:
        tasks = self.state.active_tasks
        if not tasks:
            return ""
        running = [tk for tk in tasks if tk.status == "running"]
        if running:
            items = []
            for tk in running:
                glyph = "●"
                tag = "CHILD" if tk.kind == "child_agent" else "CORE"
                step = self.state.scanner_step + tk.scanner_phase
                scan = DEFAULT_THEME.scanner_frame(step, 8)
                items.append(f"{glyph} [{tag}:{tk.name[:14]}] [{scan}] {tk.progress}%")
            return " " + " | ".join(items) + "  (Ctrl+X A for dashboard)"
        else:
            done = [tk for tk in tasks if tk.status == "done"]
            err = [tk for tk in tasks if tk.status == "error"]
            if self.state.status_text.startswith("✔") or "COMPLETED" in self.state.status_text:
                recovered = f" | {len(err)} tentativa(s) recuperada(s)" if err else ""
                return f" ✔ [PROCESSO CONCLUÍDO] {len(done)} tarefa(s)/agente(s) finalizados com sucesso{recovered}!"
            if err:
                return f" ✖ [FALHA NO PROCESSO] {len(err)} tarefa(s) com erro | {len(done)} concluída(s)"
            return f" ✔ [PROCESSO CONCLUÍDO] {len(done)} tarefa(s)/agente(s) finalizados com sucesso!"

    def _is_local_or_no_auth_provider(self, *args, **kwargs):
        return _provider_flow._is_local_or_no_auth_provider(self, *args, **kwargs)

    async def _apply_selected_model(self, *args, **kwargs):
        return await _provider_flow._apply_selected_model(self, *args, **kwargs)

    def _show_result(self, *args, **kwargs):
        return _runtime_actions._show_result(self, *args, **kwargs)

    async def _show_history(self, *args, **kwargs):
        return await _runtime_actions._show_history(self, *args, **kwargs)

    async def _show_active_history(self, *args, **kwargs):
        return await _runtime_actions._show_active_history(self, *args, **kwargs)

    async def _load_conversation(self, *args, **kwargs):
        return await _runtime_actions._load_conversation(self, *args, **kwargs)

    async def _execute_direct_tool(self, *args, **kwargs):
        return await _runtime_actions._execute_direct_tool(self, *args, **kwargs)

    async def _switch_workspace(self, *args, **kwargs):
        return await _runtime_actions._switch_workspace(self, *args, **kwargs)

    async def _set_reasoning_effort(self, *args, **kwargs):
        return await _runtime_actions._set_reasoning_effort(self, *args, **kwargs)

    async def _set_autonomy_profile(self, *args, **kwargs):
        return await _runtime_actions._set_autonomy_profile(self, *args, **kwargs)

    async def _clear_remembered_approvals(self, *args, **kwargs):
        return await _runtime_actions._clear_remembered_approvals(self, *args, **kwargs)

    async def resolve_approval(self, *args, **kwargs):
        return await _runtime_actions.resolve_approval(self, *args, **kwargs)

    def open_overlay(self, name: str, control=None, parent_name: str | None = None) -> None:
        self.overlay_manager.open(name, control, parent_name=parent_name)

    def close_overlay(self) -> None:
        self.overlay_manager.close()

    async def _open_session_picker_overlay(self) -> None:
        await self.session_picker_model.reload()
        self.open_overlay("session_picker", self.session_picker_control)

    async def _open_timeline_overlay(self) -> None:
        await self.timeline_model.reload(self.state.active_conversation_id)
        self.open_overlay("timeline", self.timeline_control)

    async def _open_diff_overlay(self) -> None:
        await self.diff_model.reload()
        self.open_overlay("diff", self.diff_control)

    def request_exit(self) -> None:
        if getattr(self, "_remote_server", None):
            try:
                self._remote_server.stop()
            except Exception:
                pass
            self._remote_server = None
        if self.application and not self.application.is_done:
            self.application.exit(result=0)

    def _key_bindings(self):
        from kitt.ui.keybindings import build_key_bindings
        return build_key_bindings(self)

    def _toggle_sidebar(self):
        self.state.sidebar_open = not self.state.sidebar_open
        if self.application: self.application.invalidate()

    def _palette_changed(self):
        self.palette_index = 0
        if self.application: self.application.invalidate()

    def _move_palette(self, amount: int) -> None:
        matches = self.commands.search(self.palette_buffer.text)
        self.palette_index = (self.palette_index + amount) % max(1, len(matches))
        if self.application: self.application.invalidate()

    async def _move_model_role(self, amount: int) -> None:
        self.model_setup_model.move_role(amount)
        self.model_setup_model.base_url_override = None
        profile = self._profile_for_role(self.model_setup_model.selected_role)
        if profile and profile.backend in self.model_setup_model.providers:
            self.model_setup_model.provider_index = self.model_setup_model.providers.index(profile.backend)
        provider = self.model_setup_model.selected_provider
        base_url = profile.base_url if profile and profile.backend == provider else self._provider_defaults(provider)[0]
        self.model_setup_model.models = await self._models_for_provider(provider, base_url)
        selected = self._model_for_role(self.model_setup_model.selected_role)
        if selected in self.model_setup_model.models:
            self.model_setup_model.model_index = self.model_setup_model.models.index(selected)
        else:
            self.model_setup_model.model_index = 0
        if self.application:
            self.application.invalidate()

    async def _move_model_provider(self, amount: int) -> None:
        self.model_setup_model.move_provider(amount)
        self.model_setup_model.base_url_override = None
        provider = self.model_setup_model.selected_provider
        base_url, _ = self._provider_defaults(provider)
        self.model_setup_model.models = await self._models_for_provider(provider, base_url)
        selected = self._model_for_role(self.model_setup_model.selected_role)
        if selected in self.model_setup_model.models:
            self.model_setup_model.model_index = self.model_setup_model.models.index(selected)
        else:
            self.model_setup_model.model_index = 0
        if self.application:
            self.application.invalidate()

    def _new_conversation(self) -> None:
        conversation = self.runtime.history.new_conversation()
        self.state.active_conversation_id = conversation["id"]
        self.state.route = "home"
        self.state.transcript.clear()
        self.explicit_files.clear()
        self.prompt_buffer.reset()
        if self.application: self.application.invalidate()

    async def _run_selected_palette(self):
        matches = self.commands.search(self.palette_buffer.text)
        if matches:
            self.close_overlay()
            await self._execute_command(matches[self.palette_index].aliases[0])

    async def _animate(self):
        while not self._shutdown:
            if (self.state.route == "home" or self.state.is_thinking or self.state.active_agent_count() > 0) and not self.no_animation:
                self.state.scanner_step += 1
                if self.application: self.application.invalidate()
            await asyncio.sleep(0.1)

    async def run_async(self) -> int:
        app = self.application or self.build_application()
        if not self.no_animation and os.environ.get("TERM") != "dumb":
            self._animation_task = asyncio.create_task(self._animate(), name="kitt-scanner")
        try:
            return int(await app.run_async() or 0)
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        if self._shutdown: return
        self._shutdown = self.state.shutting_down = True
        if self._animation_task:
            self._animation_task.cancel()
            await asyncio.gather(self._animation_task, return_exceptions=True)
        if self.bridge:
            await self.bridge.shutdown()
        app = self.application
        if app is not None:
            for stream_name in ("input", "output"):
                stream = getattr(app, stream_name, None)
                closer = getattr(stream, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        pass
        self._blocking_executor.shutdown(wait=True, cancel_futures=True)

    def _home_text(self):
        scanner = DEFAULT_THEME.scanner_frame(self.state.scanner_step, 36)
        return [
            ("class:primary.bright", "┌──────────────────────────────────────────────────────────────┐\n"),
            ("class:primary.bright", f"│  [ {scanner} ]  │\n"),
            ("class:primary.bright", "└──────────────────────────────────────────────────────────────┘\n"),
            ("class:primary", "██╗  ██╗    ██╗    ████████╗   ████████╗\n"),
            ("class:primary", "██║ ██╔╝    ██║    ╚══██╔══╝   ╚══██╔══╝\n"),
            ("class:primary", "█████╔╝     ██║       ██║         ██║   \n"),
            ("class:primary", "██╔═██╗     ██║       ██║         ██║   \n"),
            ("class:primary", "██║  ██╗    ██║       ██║         ██║   \n"),
            ("class:primary", "╚═╝  ╚═╝    ╚═╝       ╚═╝         ╚═╝   \n"),
            ("class:primary", "K.I.T.T. "),
            ("class:text.muted", f"— Knowledge & Inference Task Tool • v{_agent_version()}\n"),
            ("class:accent", f"{self.state.workspace_path}\n"),
            ("class:text.muted", f"Models: {self.state.small_model} (Context) • {self.state.large_model} (Execute)")
        ]

    def _header_text(self):
        model_name = self.state.large_model or "execution"
        mode_tag = ("class:warning", " [PLAN MODE] ") if self.state.planning_mode else ("class:status", f" [{model_name}] ")
        return [
            ("class:primary", " K.I.T.T. "),
            ("class:text.muted", f" {self.state.workspace_path} "),
            mode_tag,
            ("class:primary", f" 🧠 Reasoning: {self.state.reasoning_effort}% (Ctrl+←/→) "),
        ]

    def _transcript_text(self):
        out = []
        labels = {"user": "YOU", "assistant": "K.I.T.T.", "tool": "TOOL", "error": "ERROR", "system": "SYSTEM", "thought": "THOUGHT"}
        now = time.time()
        for block in self.state.transcript:
            if block.kind in {"tool", "thought"}:
                text = block.text
                if block.status == "running":
                    if self.state.active_turn_id or self.state.is_thinking or self.state.is_executing_tool:
                        elapsed = int(now - block.started_at) if block.started_at else 0
                        if block.kind == "thought":
                            text = f"▸ Pensando ({elapsed}s...)"
                        else:
                            text = f"{text} ({elapsed}s...)"
                    else:
                        block.status = "done"

                if block.collapsed:
                    first_line = text.split("\n")[0]
                    out.append((f"class:{block.kind}", f"{first_line} (ctrl+o para expandir)\n"))
                elif "full_output" in block.metadata:
                    out.append((f"class:{block.kind}", f"{text}\n    {block.metadata['full_output']}\n    (ctrl+o para recolher)\n"))
                else:
                    out.append((f"class:{block.kind}", f"{text}\n"))
            else:
                label = labels.get(block.kind, block.kind.upper())
                out += [(f"class:{block.kind}", f"\n{label}  "), ("class:text", block.text + "\n")]
        if self.state.unseen_output:
            out.append(("class:warning", "\n[new output below]"))
        if not out:
            return [
                ("class:primary", "  ┌─────────────────────────────────────────────────────────────────────────────┐\n"),
                ("class:error",   "  │  [ ░▒▓████████████████████████████████████████████████████████████████▓▒░ ]  │\n"),
                ("class:primary", "  └─────────────────────────────────────────────────────────────────────────────┘\n"),
                ("class:error",   "   ██╗  ██╗    ██╗    ████████╗   ████████╗\n"),
                ("class:error",   "   ██║ ██╔╝    ██║    ╚══██╔══╝   ╚══██╔══╝\n"),
                ("class:error",   "   █████╔╝     ██║       ██║         ██║   \n"),
                ("class:error",   "   ██╔═██╗     ██║       ██║         ██║   \n"),
                ("class:error",   "   ██║  ██╗    ██║       ██║         ██║   \n"),
                ("class:error",   "   ╚═╝  ╚═╝    ╚═╝       ╚═╝         ╚═╝   \n"),
                ("class:primary", "  K.I.T.T. "),
                ("class:text.muted", "— Knowledge & Inference Task Tool • Autonomous AI Coding Agent\n"),
                ("class:text.muted", "  Digite sua instrução abaixo ou /help para ver a lista de comandos.\n\n"),
            ]
        return out

    def _transcript_cursor_position(self):
        from prompt_toolkit.data_structures import Point
        if not self.state.follow_tail:
            return None
        if not self.state.transcript:
            return Point(x=0, y=0)
        text_content = self._transcript_text()
        total_lines = 0
        for style, txt in text_content:
            total_lines += txt.count("\n")
        return Point(x=0, y=max(0, total_lines - 1))

    def _sidebar_text(self):
        pct = min(100, self.state.tokens_used * 100 // max(1, self.state.context_window))
        files_section = ""
        if self.explicit_files:
            files_lines = "\n".join(f"  • {f}" for f in sorted(self.explicit_files))
            files_section = f"\n\n ATTACHED FILES ({len(self.explicit_files)})\n{files_lines}"
        else:
            files_section = "\n\n ATTACHED FILES\n  (none - use @file or /add)"
        return (
            f" WORKSPACE\n {self.state.workspace_name}\n\n"
            f" CONVERSATION\n {(self.state.active_conversation_id or 'new')[:12]}\n\n"
            f" MODELS\n {self.state.small_model}\n {self.state.large_model}\n"
            f" 🧠 Reasoning: {self.state.reasoning_effort}%\n\n"
            f" CONTEXT\n {self.state.tokens_used}/{self.state.context_window} ({pct}%)\n"
            f" SAVED {self.state.net_saved_tokens}"
            f"{files_section}"
        )

    def _status_text(self):
        pct = min(100, self.state.tokens_used * 100 // max(1, self.state.context_window))
        plan_badge = "[PLAN] " if self.state.planning_mode else ""
        if self.state.is_thinking:
            elapsed = max(0, int(time.time() - self.state.turn_started_at))
            active = next((t for t in self.state.active_tasks if t.status == "running"), None)
            detail = active.summary if active else "processando solicitação"
            return f" {plan_badge}{self.state.status_text} {elapsed}s | {detail[:48]} | context {pct}% "
        if self.state.width < 80:
            branch_part = (
                f" | branch:{self.state.current_branch[:12]}"
                if self.state.current_branch
                else ""
            )
            return f" {plan_badge}{self.state.status_text}{branch_part} | {self.state.large_model[:16]} | {pct}% "
        branch_part = (
            f" | branch:{self.state.current_branch}"
            if self.state.current_branch
            else ""
        )
        return (
            f" {self.state.workspace_name}{branch_part} | "
            f"{plan_badge}{self.state.status_text} | "
            f"{self.state.large_model} | context {pct}% "
        )

    def _context_details_text(self) -> str:
        cs = self.state.context_stats
        total = cs.selected_count + cs.rejected_count
        lines = [
            "◈ DETALHES DO MOTOR DE CONTEXTO / CONTEXT ENGINE ◈",
            f"• Estado do Índice: {cs.index_state or 'READY'} (Geração: {cs.index_generation})",
            f"• Candidatos: {cs.selected_count} selecionados / {cs.rejected_count} rejeitados (Total: {total})",
            f"• Cobertura: {cs.coverage:.0%}{' [DEGRADADO]' if cs.degraded else ''}",
            f"• Tokens no Pacote: {cs.context_tokens} tokens",
            f"• Filtro Semântico: {cs.filter_source or 'N/A'}{f' ({cs.filter_fallback_reason})' if cs.filter_fallback_reason else ''} - Latência: {int(cs.filter_latency_ms)}ms",
        ]
        if cs.partial_reason:
            lines.append(f"• Motivo parcial: {cs.partial_reason}")
        if cs.index_scanned or cs.index_updated or cs.index_deleted:
            lines.append(f"• Índice: {cs.index_scanned} escaneados, {cs.index_updated} atualizados, {cs.index_deleted} removidos")
        return "\n".join(lines)

    def _toast_text(self) -> str:
        toasts = self.state.active_toasts()
        if not toasts:
            return ""
        t = toasts[-1]
        if self.state.active_overlay is None and not self.prompt_buffer.text.strip():
            return f" {t.text}\n  [Esc/Enter: Fechar Aviso]"
        return f" {t.text}"

    def _permission_text(self):
        from kitt.ui.components.permission_card import PermissionCardComponent
        return PermissionCardComponent().render(
            self.state, max(50, self.state.width - 10), self.approval_menu_index
        )

    def _autonomy_text(self) -> str:
        t = DEFAULT_THEME
        curr = self.runtime.autonomy_store.get()
        command_mode = (
            "DENY" if curr.level == "read_only"
            else "ALLOW ALL" if curr.allow_run_command_auto
            else "ASK"
        )
        rules = getattr(self.runtime.approval, "remembered_rules", [])
        rules_str = "\n".join(f"  • {r.tool_name} ({r.path_glob or '*'}) -> {r.decision.upper()} [{r.scope}]" for r in rules[-5:]) if rules else "  (Nenhuma regra salva)"

        return (
            t.format_primary("┌── CENTRAL DE PERMISSÕES & AUTONOMIA / AUTONOMY CONTROL ───────────────────┐\n") +
            f"│ Perfil Atual: [ {curr.level.upper()} ]  Comandos: [ {command_mode} ]\n" +
            "│\n" +
            "│ Política para comandos e alterações:\n" +
            "│  [1] ALLOW ALL : Executar automaticamente dentro das regras críticas\n" +
            "│  [2] ASK       : Pedir aprovação antes de comandos e alterações\n" +
            "│  [3] DENY      : Bloquear comandos, alterações e subagentes\n" +
            "│\n" +
            "│ Regras Salvas no Workspace:\n" +
            f"{rules_str}\n" +
            "│\n" +
            "│ Controles: [1] Allow All  [2] Ask  [3] Deny  [r] Limpar Regras  [Esc] Sair\n" +
            t.format_primary("└────────────────────────────────────────────────────────────────────────────┘")
        )

    def _palette_text(self):
        from kitt.ui.components.command_palette import CommandPaletteComponent
        return CommandPaletteComponent(self.commands, self.keymap).render(
            query=self.palette_buffer.text,
            selected_index=self.palette_index,
            width=max(40, self.state.width - 16),
            window_size=10,
        )

    def _session_picker_text(self):
        sessions = self.session_picker_model.sessions
        if not sessions:
            q = self.session_picker_model.query.strip()
            if q:
                return f"  Nenhuma conversa encontrada para '{q}'.\n  Limpe a busca ou tente outro termo."
            return "  Nenhuma conversa anterior encontrada.\n  Inicie uma nova conversa para salvar o histórico."
        total = len(sessions)
        lines = [f"Buscar Conversas ({total} salvas)  (Enter: Retomar  |  Esc: Voltar)\n"]
        window_size = 12
        start = min(max(0, self.session_picker_model.selected_index - (window_size // 2)), max(0, total - window_size))
        end = min(total, start + window_size)

        if start > 0:
            lines.append(f"  ▲ ... ({start} conversas anteriores)")

        for idx in range(start, end):
            s = sessions[idx]
            prefix = "> " if idx == self.session_picker_model.selected_index else "  "
            lines.append(f"{prefix}[{idx+1}/{total}] {s.get('id', '')[:8]}  {s.get('title', 'Sem título')}")

        if end < total:
            lines.append(f"  ▼ ... ({total - end} conversas mais antigas)")
        return "\n".join(lines)

    def _timeline_text(self):
        turns = self.timeline_model.turns
        if not turns:
            return "  Nenhum turno registrado na conversa ativa."
        total = len(turns)
        lines = [f"Linha do Tempo ({total} turnos)  (Esc: Voltar)\n"]
        window_size = 12
        start = min(max(0, self.timeline_model.selected_index - (window_size // 2)), max(0, total - window_size))
        end = min(total, start + window_size)

        if start > 0:
            lines.append(f"  ▲ ... ({start} turnos anteriores)")

        for idx in range(start, end):
            t = turns[idx]
            prefix = "> " if idx == self.timeline_model.selected_index else "  "
            lines.append(f"{prefix}[{idx+1}/{total}] Turno {t.get('ordinal', idx+1)} ({t.get('id', '')[:8]})")

        if end < total:
            lines.append(f"  ▼ ... ({total - end} turnos seguintes)")
        return "\n".join(lines)

    def _diff_text(self):
        diff = self.diff_model.diff_text
        if not diff:
            return "Unified diff preview\n\nNo pending diff."
        lines = diff.splitlines()[self.diff_model.scroll_offset:self.diff_model.scroll_offset + 30]
        return "Unified diff preview (Use Up/Down to scroll)\n\n" + "\n".join(lines)

    def _model_setup_header_text(self) -> str:
        setup = self.model_setup_model
        lines = [
            " [Tab] Alternar Cargo  |  [T] Limites Locais  |  [P / Espaço] Menu Provedores (★)  |  [L] Login/Auth  |  [Enter] Selecionar  |  [Esc] Fechar",
            " Atribuições de Modelos por Cargo:"
        ]
        for role in setup.roles:
            marker = ">" if role == setup.selected_role else " "
            profile = self._profile_for_role(role)
            endpoint = profile.base_url if profile else "?"
            enforced = getattr(profile, "enforce_local_limits", True) if profile else True
            lim_badge = "[Lim: On]" if enforced else "[Lim: Off]"
            lines.append(f" {marker} {role.title():10} {(profile.backend if profile else '?')}/{self._model_for_role(role)} @ {endpoint} {lim_badge}")
        
        profile = self._profile_for_role(setup.selected_role)
        endpoint = setup.base_url_override or (profile.base_url if (profile and profile.backend == setup.selected_provider) else self._provider_defaults(setup.selected_provider)[0])
        star = "★" if setup.selected_provider in setup.favorite_providers else "☆"

        from kitt.llm.auth import ProviderAuthService
        auth_service = ProviderAuthService()
        is_auth = bool(auth_service.resolve(None, setup.selected_provider))
        if self._is_local_or_no_auth_provider(setup.selected_provider, endpoint):
            auth_badge = "[◌ Local / Sem Token Necessário]"
        elif is_auth:
            auth_badge = "[● Conectado / Autenticado]"
        else:
            auth_badge = "[○ Não autenticado — L: Conectar]"

        src_badge = f"(Origem: {setup.source})" if hasattr(setup, "source") and setup.source else ""
        lines.append(f" Provedor Selecionado: {star} {setup.selected_provider} @ {endpoint} {auth_badge} {src_badge}")
        return "\n".join(lines)

    def _model_setup_text(self):
        setup = self.model_setup_model
        if getattr(setup, "loading", False):
            return "  ◌ Carregando lista de modelos do provedor..."

        if getattr(setup, "error_message", None):
            return (
                f"  ⚠ Não foi possível consultar modelos do provedor '{setup.selected_provider}'.\n"
                f"  Motivo: {setup.error_message}\n\n"
                "  [Enter] Tentar novamente  |  [E] Editar endpoint  |  [Esc] Voltar"
            )

        filtered = setup.get_filtered_models()
        total_models = len(filtered)
        all_models = len(setup.models)
        
        filter_tag = f" (Filtrando {total_models}/{all_models})" if setup.search_query.strip() else f" ({all_models} modelos)"
        lines = [f"Modelos Disponíveis{filter_tag}:"]
        
        if not filtered:
            if setup.search_query.strip():
                lines.append(f"\n  Nenhum modelo encontrado para o filtro '{setup.search_query}'.\n  Limpe o filtro de busca.")
            else:
                lines.append(f"\n  Nenhum modelo reportado pelo provedor '{setup.selected_provider}'.\n  [E] Configurar endpoint  |  [Esc] Voltar")
            return "\n".join(lines)

        window_size = 14
        start = min(max(0, setup.model_index - (window_size // 2)), max(0, total_models - window_size))
        end = min(total_models, start + window_size)

        if start > 0:
            lines.append(f"  ▲ ... ({start} modelos acima)")

        for index in range(start, end):
            model = filtered[index]
            marker = ">" if index == setup.model_index else " "
            badge = setup.format_model_badge(setup.selected_provider, model)
            lines.append(f"{marker} [{index+1}/{total_models}] {model:<34}{badge}")

        if end < total_models:
            lines.append(f"  ▼ ... ({total_models - end} modelos abaixo)")
        return "\n".join(lines)

    @staticmethod
    def _provider_endpoint_text():
        return "Informe a URL do endpoint remoto (ex: http://192.168.1.50:11434):\n[Enter] Descobrir Modelos  |  [Esc] Cancelar\n"

    def _help_text(self):
        shortcuts = ["ATALHOS — Ctrl+P descobre todas as ações", "Mouse ativo por padrão: role sobre o painel desejado; /mouse alterna para seleção nativa.", ""]
        shortcuts.extend(f"{keys:22} {description}" for _, keys, description in self.keymap.get_help_list())
        shortcuts.extend(["", "COMANDOS", ""])
        shortcuts.extend(f"{c.aliases[0]:22} {c.description}" for c in self.commands.commands.values())
        return "\n".join(shortcuts)
