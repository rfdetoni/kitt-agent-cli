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
from kitt.ui import mouse as _mouse
from kitt.ui.render import core as _render_core
from kitt.ui.render import overlays as _render_overlays


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
        self._blocking_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="kitt-ui-blocking")
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

        # Register the local KITT Reverse Proxy descriptor without probing the network.
        # Availability is checked lazily when the provider is selected/used, keeping TUI
        # construction deterministic and eliminating a startup timeout from the hot path.
        try:
            if hasattr(self.runtime.processor, "registry"):
                from kitt.llm.catalog import ProviderDescriptor
                proxy_url = os.environ.get("KITT_REVERSE_PROXY_URL", "http://127.0.0.1:3000")
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

    def _model_setup_mouse_handler(self, *args, **kwargs):
        return _mouse._model_setup_mouse_handler(self, *args, **kwargs)

    def _provider_popup_mouse_handler(self, *args, **kwargs):
        return _mouse._provider_popup_mouse_handler(self, *args, **kwargs)

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

    def _agents_text(self, *args, **kwargs):
        return _render_overlays._agents_text(self, *args, **kwargs)

    def _live_agents_text(self, *args, **kwargs):
        return _render_overlays._live_agents_text(self, *args, **kwargs)

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
            if (self.state.is_thinking or self.state.active_agent_count() > 0) and not self.no_animation:
                self.state.scanner_step += 1
                if self.application:
                    self.application.invalidate()
            await asyncio.sleep(0.15)

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

    def _home_text(self, *args, **kwargs):
        return _render_core._home_text(self, *args, **kwargs)

    def _header_text(self, *args, **kwargs):
        return _render_core._header_text(self, *args, **kwargs)

    def _transcript_text(self, *args, **kwargs):
        return _render_core._transcript_text(self, *args, **kwargs)

    def _transcript_cursor_position(self, *args, **kwargs):
        return _render_core._transcript_cursor_position(self, *args, **kwargs)

    def _sidebar_text(self, *args, **kwargs):
        return _render_core._sidebar_text(self, *args, **kwargs)

    def _status_text(self, *args, **kwargs):
        return _render_core._status_text(self, *args, **kwargs)

    def _context_details_text(self, *args, **kwargs):
        return _render_core._context_details_text(self, *args, **kwargs)

    def _toast_text(self, *args, **kwargs):
        return _render_core._toast_text(self, *args, **kwargs)

    def _permission_text(self, *args, **kwargs):
        return _render_overlays._permission_text(self, *args, **kwargs)

    def _autonomy_text(self, *args, **kwargs):
        return _render_overlays._autonomy_text(self, *args, **kwargs)

    def _palette_text(self, *args, **kwargs):
        return _render_overlays._palette_text(self, *args, **kwargs)

    def _session_picker_text(self, *args, **kwargs):
        return _render_overlays._session_picker_text(self, *args, **kwargs)

    def _timeline_text(self, *args, **kwargs):
        return _render_overlays._timeline_text(self, *args, **kwargs)

    def _diff_text(self, *args, **kwargs):
        return _render_overlays._diff_text(self, *args, **kwargs)

    def _model_setup_header_text(self, *args, **kwargs):
        return _render_overlays._model_setup_header_text(self, *args, **kwargs)

    def _model_setup_text(self, *args, **kwargs):
        return _render_overlays._model_setup_text(self, *args, **kwargs)

    @staticmethod
    def _provider_endpoint_text():
        return "Informe a URL do endpoint remoto (ex: http://192.168.1.50:11434):\n[Enter] Descobrir Modelos  |  [Esc] Cancelar\n"

    def _help_text(self, *args, **kwargs):
        return _render_overlays._help_text(self, *args, **kwargs)
