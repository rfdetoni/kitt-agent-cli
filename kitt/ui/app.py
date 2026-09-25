from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import os
import re
from pathlib import Path
from typing import Any, Optional

from kitt.core.turn_events import ApprovalRequired
from kitt.ui.commands import CommandRegistry
from kitt.ui.event_bridge import TurnEventBridge
from kitt.ui.git import read_git_branch_name
from kitt.ui.keymap import KeyMap
from kitt.ui.layout import LayoutDimensions, build_root_container
from kitt.ui.overlay_models import DiffViewerModel, ModelSetupModel, OverlayFrame, SessionPickerModel, TimelineModel
from kitt.ui.reducer import reduce_ui_event
from kitt.ui.state import UIState
from kitt.ui.theme import DEFAULT_THEME
from kitt.ui.overlay_manager import OverlayManager
from kitt.ui import command_dispatcher as _command_dispatcher
from kitt.ui import model_service as _model_service
from kitt.ui import provider_flow as _provider_flow
from kitt.ui import runtime_actions as _runtime_actions
from kitt.ui import mouse as _mouse
from kitt.ui import navigation as _navigation\nfrom kitt.ui import reverse_proxy_panel as _reverse_proxy_panel\nfrom kitt.reverse_proxy.client import ReverseProxyClient
from kitt.ui.render import core as _render_core
from kitt.ui.render import overlays as _render_overlays


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
        self.model_setup_model = ModelSetupModel()\n        self.reverse_proxy_model = _reverse_proxy_panel.ReverseProxyPanelModel()\n        self.reverse_proxy_client = ReverseProxyClient()
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

    _execute_command = _command_dispatcher._execute_command
    # Service contract aliases. Module-level functions bind to this instance when
    # exposed on the class, preserving the internal KITT UI contract without
    # reintroducing orchestration logic into KittUIApp.
    _parse_model_command = _model_service._parse_model_command
    _role_tasks = _model_service._role_tasks
    _model_for_role = _model_service._model_for_role
    _profile_for_role = _model_service._profile_for_role
    _set_model_role = _model_service._set_model_role
    _toggle_role_local_limits = _model_service._toggle_role_local_limits
    _models_for_provider = _model_service._models_for_provider
    _prepare_model_setup = _model_service._prepare_model_setup
    _model_setup_search_changed = _model_service._model_setup_search_changed
    _open_model_setup_overlay = _model_service._open_model_setup_overlay

    _model_setup_mouse_handler = _mouse.model_setup_mouse_handler
    _provider_popup_mouse_handler = _mouse.provider_popup_mouse_handler

    _select_popup_action = _provider_flow._select_popup_action
    _open_provider_popup_overlay = _provider_flow._open_provider_popup_overlay
    _provider_popup_text = _provider_flow._provider_popup_text
    _persist_custom_providers = _provider_flow._persist_custom_providers
    _open_add_provider_overlay = _provider_flow._open_add_provider_overlay
    _open_edit_provider_overlay = _provider_flow._open_edit_provider_overlay
    _delete_custom_provider = _provider_flow._delete_custom_provider
    _add_provider_help_text = _provider_flow._add_provider_help_text
    _accept_add_provider = _provider_flow._accept_add_provider
    _finish_add_provider = _provider_flow._finish_add_provider
    _open_provider_endpoint_overlay = _provider_flow._open_provider_endpoint_overlay
    _submit_provider_endpoint = _provider_flow._submit_provider_endpoint
    _auth_login_help_text = _provider_flow._auth_login_help_text
    _start_oauth_flow = _provider_flow._start_oauth_flow
    _accept_model_setup_search = _provider_flow._accept_model_setup_search
    _open_auth_login_overlay = _provider_flow._open_auth_login_overlay
    _accept_auth_login = _provider_flow._accept_auth_login
    _apply_pending_model = _provider_flow._apply_pending_model
    _is_local_or_no_auth_provider = _provider_flow._is_local_or_no_auth_provider
    _apply_selected_model = _provider_flow._apply_selected_model

    _open_reverse_proxy_overlay = _reverse_proxy_panel._open_reverse_proxy_overlay
    _refresh_reverse_proxy = _reverse_proxy_panel._refresh_reverse_proxy
    _reverse_proxy_move = _reverse_proxy_panel._reverse_proxy_move
    _reverse_proxy_cycle_profile = _reverse_proxy_panel._reverse_proxy_cycle_profile
    _reverse_proxy_show = _reverse_proxy_panel._reverse_proxy_show
    _reverse_proxy_prepare_url = _reverse_proxy_panel._reverse_proxy_prepare_url
    _reverse_proxy_prepare_profile_create = _reverse_proxy_panel._reverse_proxy_prepare_profile_create
    _reverse_proxy_start_selected = _reverse_proxy_panel._reverse_proxy_start_selected
    _reverse_proxy_stop_selected = _reverse_proxy_panel._reverse_proxy_stop_selected
    _reverse_proxy_restart_selected = _reverse_proxy_panel._reverse_proxy_restart_selected
    _reverse_proxy_remove_profile = _reverse_proxy_panel._reverse_proxy_remove_profile
    _reverse_proxy_bind_selected = _reverse_proxy_panel._reverse_proxy_bind_selected

    _show_result = _runtime_actions._show_result
    _show_history = _runtime_actions._show_history
    _show_active_history = _runtime_actions._show_active_history
    _load_conversation = _runtime_actions._load_conversation
    _execute_direct_tool = _runtime_actions._execute_direct_tool
    _switch_workspace = _runtime_actions._switch_workspace
    _set_reasoning_effort = _runtime_actions._set_reasoning_effort
    _set_autonomy_profile = _runtime_actions._set_autonomy_profile
    _clear_remembered_approvals = _runtime_actions._clear_remembered_approvals
    resolve_approval = _runtime_actions.resolve_approval
    _export_conversation = _runtime_actions._export_conversation
    _provider_defaults = staticmethod(_model_service._provider_defaults)
    _scroll_transcript = _mouse._scroll_transcript
    toggle_mouse_support = _mouse.toggle_mouse_support
    toggle_turn_mode = _navigation.toggle_turn_mode
    _provider_endpoint_text = _provider_flow._provider_endpoint_text
    def open_overlay(self, name: str, control=None, parent_name: str | None = None) -> None:
        self.overlay_manager.open(name, control, parent_name=parent_name)

    def close_overlay(self) -> None:
        self.overlay_manager.close()

    _open_session_picker_overlay = _navigation._open_session_picker_overlay
    _open_timeline_overlay = _navigation._open_timeline_overlay
    _open_diff_overlay = _navigation._open_diff_overlay
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

    _toggle_sidebar = _navigation._toggle_sidebar
    _palette_changed = _navigation._palette_changed
    _move_palette = _navigation._move_palette
    _move_model_role = _navigation._move_model_role
    _move_model_provider = _navigation._move_model_provider
    _new_conversation = _navigation._new_conversation
    _run_selected_palette = _navigation._run_selected_palette
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

    _home_text = _render_core._home_text
    _header_text = _render_core._header_text
    _transcript_text = _render_core._transcript_text
    _transcript_cursor_position = _render_core._transcript_cursor_position
    _sidebar_text = _render_core._sidebar_text
    _status_text = _render_core._status_text
    _context_details_text = _render_core._context_details_text
    _toast_text = _render_core._toast_text
    _permission_text = _render_overlays._permission_text
    _autonomy_text = _render_overlays._autonomy_text
    _palette_text = _render_overlays._palette_text
    _session_picker_text = _render_overlays._session_picker_text
    _timeline_text = _render_overlays._timeline_text
    _diff_text = _render_overlays._diff_text
    _model_setup_header_text = _render_overlays._model_setup_header_text
    _model_setup_text = _render_overlays._model_setup_text
    _agents_text = _render_overlays._agents_text
    _live_agents_text = _render_overlays._live_agents_text
    _reverse_proxy_text = _reverse_proxy_panel._reverse_proxy_text\n    _help_text = _render_overlays._help_text
