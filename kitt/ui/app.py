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

from kitt.core.turn_events import ApprovalRequired
from kitt.ui.commands import CommandRegistry
from kitt.ui.event_bridge import TurnEventBridge
from kitt.ui.git import read_git_branch_name
from kitt.ui.layout import LayoutDimensions, build_root_container
from kitt.ui.overlay_models import DiffViewerModel, ModelSetupModel, OverlayFrame, SessionPickerModel, TimelineModel
from kitt.ui.reducer import reduce_ui_event
from kitt.ui.state import UIState, safe_text
from kitt.ui.theme import DEFAULT_THEME
from kitt.ui.model_commands import (
    handle_model_command, handle_setup_models_command, handle_add_provider_command,
    handle_edit_provider_command, handle_delete_provider_command, parse_model_command
)
from kitt.ui.session_commands import (
    handle_resume_command, handle_fork_command, handle_export_command,
    handle_compact_command, handle_stats_command, handle_status_command
)
from kitt.ui.skill_commands import (
    handle_setup_skills_command, handle_skill_install_command, handle_skill_remove_command,
    handle_remember_command, handle_clear_memory_command, handle_doctor_command
)
from kitt.ui.dream_commands import handle_dream_command, handle_memory_extended_command
from kitt.ui.overlay_manager import OverlayManager


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

        self._init_models_from_runtime()
        self.commands = CommandRegistry()
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

    @property
    def dimensions(self) -> LayoutDimensions:
        return LayoutDimensions(self.state.width, self.state.height)

    def _build_controls(self) -> None:
        from prompt_toolkit.buffer import Buffer
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl

        ui = self

        class KittCompleter(Completer):
            def get_completions(self, document, complete_event):
                from kitt.skills.discovery import SkillDiscovery
                word = document.get_word_before_cursor(WORD=True)
                if not word:
                    return

                roots = [
                    Path(ui.state.workspace_path) / ".kitt" / "skills",
                    Path(ui.state.workspace_path) / ".gemini" / "skills",
                    Path.home() / ".kitt" / "skills",
                    Path.home() / ".gemini" / "config" / "plugins",
                    Path.home() / ".gemini" / "antigravity-cli" / "builtin" / "skills",
                    Path.home() / ".claude" / "plugins",
                ]

                if word.startswith("/"):
                    # 1. Built-in slash commands
                    seen = set()
                    for command in ui.commands.search(word):
                        alias = command.aliases[0]
                        seen.add(alias)
                        yield Completion(alias, start_position=-len(word), display_meta=command.description)

                    # 2. Dynamic Skills and Subskills
                    try:
                        discovery = SkillDiscovery()
                        for cmd, meta in discovery.get_skill_completions(roots):
                            if cmd not in seen and (cmd.lower().startswith(word.lower()) or word.lower() in cmd.lower()):
                                seen.add(cmd)
                                yield Completion(cmd, start_position=-len(word), display_meta=meta)
                    except Exception:
                        pass

                elif word.startswith("@"):
                    prefix = word[1:]
                    # 1. Files & Directories in workspace
                    try:
                        for path in Path(ui.state.workspace_path).glob(prefix + "*"):
                            name = str(path.relative_to(ui.state.workspace_path)) + ("/" if path.is_dir() else "")
                            yield Completion("@" + name, start_position=-len(word), display_meta="file")
                    except Exception:
                        pass

                    # 2. Mentionable Skills
                    try:
                        discovery = SkillDiscovery()
                        for skill in discovery.discover(roots):
                            at_cmd = f"@{skill.name}"
                            if at_cmd.lower().startswith(word.lower()) or word.lower() in at_cmd.lower():
                                desc = skill.description[:40] if skill.description else "Skill"
                                yield Completion(at_cmd, start_position=-len(word), display_meta=f"skill: {desc}")
                    except Exception:
                        pass

        self.prompt_buffer = Buffer(
            multiline=True,
            completer=KittCompleter(),
            complete_while_typing=True,
            accept_handler=self._accept_prompt,
        )
        self.prompt_control = BufferControl(buffer=self.prompt_buffer, focusable=True)
        self.palette_buffer = Buffer(multiline=False)
        self.palette_buffer.on_text_changed += lambda _: self._palette_changed()
        self.palette_search_control = BufferControl(buffer=self.palette_buffer, focusable=True)
        self.provider_endpoint_buffer = Buffer(multiline=False, accept_handler=self._accept_provider_endpoint)
        self.provider_endpoint_control = BufferControl(buffer=self.provider_endpoint_buffer, focusable=True)
        self.home_control = FormattedTextControl(self._home_text)
        self.hints_control = FormattedTextControl(lambda: f"F4: Modo [{self.state.turn_mode.upper()}]   F12: Modelos   Ctrl+P: Comandos   Alt+Enter: Nova Linha")
        self.header_control = FormattedTextControl(self._header_text)
        self.transcript_control = FormattedTextControl(self._transcript_text, get_cursor_position=self._transcript_cursor_position, focusable=True)
        self.transcript_control.mouse_handler = self._transcript_mouse_handler
        self.sidebar_control = FormattedTextControl(self._sidebar_text)
        self.status_control = FormattedTextControl(self._status_text)
        self.permission_control = FormattedTextControl(self._permission_text, focusable=True)
        self.palette_control = FormattedTextControl(self._palette_text, focusable=True)
        self.session_picker_control = FormattedTextControl(self._session_picker_text, focusable=True)
        self.timeline_control = FormattedTextControl(self._timeline_text, focusable=True)
        self.diff_control = FormattedTextControl(self._diff_text, focusable=True)
        self.pending_model_selection: Optional[Tuple[str, str, str, Optional[str]]] = None
        self.model_setup_search_buffer = Buffer(multiline=False, accept_handler=self._accept_model_setup_search)
        self.model_setup_search_buffer.on_text_changed += lambda _: self._model_setup_search_changed()
        self.model_setup_search_control = BufferControl(buffer=self.model_setup_search_buffer, focusable=True)
        self.model_setup_header_control = FormattedTextControl(self._model_setup_header_text)
        self.model_setup_control = FormattedTextControl(self._model_setup_text, focusable=True)
        self.model_setup_control.mouse_handler = self._model_setup_mouse_handler
        self.provider_popup_control = FormattedTextControl(self._provider_popup_text, focusable=True)
        self.provider_popup_control.mouse_handler = self._provider_popup_mouse_handler
        self.add_provider_name_buffer = Buffer(multiline=False)
        self.add_provider_name_control = BufferControl(buffer=self.add_provider_name_buffer, focusable=True)
        self.add_provider_url_buffer = Buffer(multiline=False, accept_handler=self._accept_add_provider)
        self.add_provider_url_control = BufferControl(buffer=self.add_provider_url_buffer, focusable=True)
        self.add_provider_help_control = FormattedTextControl(self._add_provider_help_text)
        self.autonomy_control = FormattedTextControl(self._autonomy_text, focusable=True)
        self.agents_control = FormattedTextControl(self._agents_text, focusable=True)
        self.live_agents_control = FormattedTextControl(self._live_agents_text)
        self.target_auth_provider: str | None = None
        self.provider_endpoint_help_control = FormattedTextControl(self._provider_endpoint_text)
        self.auth_login_buffer = Buffer(multiline=False, accept_handler=self._accept_auth_login)
        self.auth_login_control = BufferControl(buffer=self.auth_login_buffer, focusable=True)
        self.auth_login_help_control = FormattedTextControl(self._auth_login_help_text)
        self.help_control = FormattedTextControl(self._help_text, focusable=True)
        self.toast_control = FormattedTextControl(self._toast_text)

    def build_application(self):
        from prompt_toolkit.application import Application
        from prompt_toolkit.cursor_shapes import CursorShape
        from prompt_toolkit.filters import Condition
        from prompt_toolkit.layout import Layout
        root = build_root_container(self)
        self.application = Application(
            layout=Layout(root, focused_element=self.prompt_control),
            key_bindings=self._key_bindings(),
            style=DEFAULT_THEME.prompt_toolkit_style(),
            full_screen=True,
            cursor=CursorShape.BLINKING_BEAM,
            mouse_support=Condition(lambda: getattr(self, "mouse_support_enabled", True)),
            refresh_interval=None,
            min_redraw_interval=1 / 30,
            input=self.input,
            output=self.output,
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

    async def _execute_command(self, raw: str) -> bool:
        parts = raw.split(maxsplit=1)
        name = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        found = self.commands.find(name)
        if not found:
            return False
        if found.id == "quit":
            self.request_exit()
        elif found.id == "clear":
            self._new_conversation()
            self._show_result("Started a clean conversation.")
        elif found.id == "help":
            self.open_overlay("help", self.help_control)
        elif found.id == "model":
            await handle_model_command(self, arg)
        elif found.id == "setup_skills":
            await handle_setup_skills_command(self, arg)
        elif found.id == "setup_models":
            await handle_setup_models_command(self, arg)
        elif found.id == "add_provider":
            await handle_add_provider_command(self, arg)
        elif found.id == "edit_provider":
            await handle_edit_provider_command(self, arg)
        elif found.id == "delete_provider":
            await handle_delete_provider_command(self, arg)
        elif found.id in {"mode", "toggle_mode"}:
            if arg:
                self.toggle_turn_mode(arg.strip())
            else:
                self.toggle_turn_mode()
        elif found.id == "new":
            self._new_conversation()
        elif found.id == "history":
            await self._show_active_history()
        elif found.id == "thread":
            await self._show_history(arg)
        elif found.id in {"resume"}:
            await handle_resume_command(self, arg)
        elif found.id == "conversation":
            conversation = self.runtime.history.get_or_create_active()
            self._show_result(f"Active conversation\n{conversation['id']}\n{conversation['title']}")
        elif found.id == "plan":
            if arg:
                if self.state.is_thinking or (self.bridge and self.bridge.is_active):
                    return True
                self.state.is_thinking = True
                conversation = self.runtime.history.get_or_create_active()
                try:
                    await self.bridge.start(arg, conversation["id"], explicit_files=self.explicit_files, no_history=not self.runtime.config.history_enabled, mode="plan")
                except Exception as err:
                    self.state.is_thinking = False
                    self.state.add_toast(f"Turn Error: {err}")
            else:
                self.state.planning_mode = not self.state.planning_mode
                status = "ATIVADO (Modo Leitura / Planejamento)" if self.state.planning_mode else "DESATIVADO (Modo Normal / Execução)"
                self._show_result(f"Modo de Planejamento: {status}")
        elif found.id == "fork":
            await handle_fork_command(self, arg)
        elif found.id in ("export", "export_conversation"):
            await handle_export_command(self, arg)
        elif found.id == "memory":
            if arg:
                await handle_memory_extended_command(self, arg)
            else:
                self._show_result(self.runtime.memory.get_memory_context() or "No memory entries.")
        elif found.id == "dream":
            await handle_dream_command(self, arg)
        elif found.id == "remember":
            await handle_remember_command(self, arg)
        elif found.id == "clear_memory":
            await handle_clear_memory_command(self)
        elif found.id == "skills":
            skills = self.runtime.skills.list_skills()
            self._show_result("\n".join(f"{s.name} v{s.version} — {s.author}" for s in skills) or "No skills installed.")
        elif found.id == "skill_install":
            await handle_skill_install_command(self, arg)
        elif found.id == "skill_remove":
            await handle_skill_remove_command(self, arg)
        elif found.id == "files":
            self._show_result("\n".join(sorted(self.explicit_files)) or "No explicit files added.")
        elif found.id == "add":
            added, missing = [], []
            for item in arg.split():
                path = Path(self.state.workspace_path, item).resolve()
                if path.exists() and path.is_file() and Path(self.state.workspace_path).resolve() in path.parents:
                    self.explicit_files.add(str(path.relative_to(self.state.workspace_path)))
                    added.append(item)
                else:
                    missing.append(item)
            self._show_result((f"Added: {', '.join(added)}" if added else "") + (f"\nNot found: {', '.join(missing)}" if missing else "") or "Usage: /add <file>")
        elif found.id == "drop":
            names = set(arg.split())
            removed = self.explicit_files.intersection(names)
            self.explicit_files.difference_update(names)
            self._show_result(f"Dropped: {', '.join(sorted(removed))}" if removed else "No matching context files.")
        elif found.id == "repomap":
            blocks = await self._run_blocking(self.runtime.processor.context_engine.get_relevant_context, "", 1024, str(self.runtime.canonical_root))
            self._show_result("\n\n".join(block.content for block in blocks) or "Repository map empty.")
        elif found.id == "doctor":
            await handle_doctor_command(self)
        elif found.id == "diff":
            await self._open_diff_overlay()
        elif found.id == "status":
            handle_status_command(self)
        elif found.id == "stats":
            await handle_stats_command(self)
        elif found.id == "context_stats":
            config = self.runtime.config
            self._show_result(f"Context window: {config.context_window_default}\nReserved output: {config.reserved_output_tokens}")
        elif found.id == "router":
            router = getattr(self.runtime.processor, "router", None)
            profiles = getattr(getattr(router, "config", None), "profiles", {})
            self._show_result("\n".join(f"{n}: {p.backend}/{p.model}" for n, p in profiles.items()) or "Router configuration unavailable.")
        elif found.id == "approvals":
            if await self._ensure_daemon_management():
                pending = await self.bridge.list_approvals()
                self._show_result("\n".join(
                    f"{str(r.get('approval_id',''))[:8]} {r.get('tool_name','')} ({str(r.get('turn_id',''))[:8]})"
                    for r in pending
                ) or "No approval requests.")
            else:
                pending = self.runtime.approval.list_pending(self.runtime.workspace_id)
                self._show_result("\n".join(f"{r.approval_id[:8]} {r.tool_name} ({r.turn_id[:8]})\n  summary: {r.summary}" for r in pending) or "No approval requests.")
        elif found.id == "compact":
            await handle_compact_command(self, arg)
        elif found.id == "child":
            if not arg:
                self._show_result("Usage: /child <task description>")
            else:
                conversation = self.runtime.history.get_or_create_active()
                if await self._ensure_daemon_management():
                    await self._execute_direct_tool("child_spawn", {"task": arg})
                else:
                    child = await self._run_blocking(
                        self.runtime.children.spawn,
                        parent_conversation_id=conversation["id"], parent_turn_id="ui", task=arg,
                    )
                    self._show_result(f"Spawned child: {child.id} ({child.state})")
        elif found.id == "tasks":
            if not self.state.active_tasks:
                self._show_result("Nenhum agente ou sub-tarefa ativo no momento.")
            else:
                lines = [f"─── MONITOR DE SUBAGENTES ({self.state.overall_progress}% Concluído) ───\n"]
                for t in self.state.active_tasks:
                    lines.append(f"• [{t.status.upper()}] {t.name} ({t.role})\n  ↳ Resumo: {t.summary}\n  ↳ Progresso: {t.progress}%")
                self._show_result("\n".join(lines))
        elif found.id in {"cancel", "stop"}:
            if self.bridge and self.bridge.is_active:
                await self.bridge.cancel("Cancelled by user via command")
                self._show_result("Active turn cancelled.")
            else:
                self._show_result("No active turn to cancel.")
        elif found.id == "reasoning":
            if arg:
                try:
                    val = max(0, min(100, int(arg.replace("%", "").strip())))
                    self.state.reasoning_effort = val
                    if hasattr(self.runtime, "processor"):
                        self.runtime.processor.reasoning_effort = val
                    if await self._ensure_daemon_management():
                        await self.bridge.set_reasoning(val)
                    blocks = int(val / 10)
                    bar = "█" * blocks + "░" * (10 - blocks)
                    self._show_result(f"Reasoning effort definido para {val}% [{bar}] (Modelo: {self.state.large_model})")
                except ValueError:
                    self._show_result("Uso: /reasoning <0-100> (ex: /reasoning 80)")
            else:
                blocks = int(self.state.reasoning_effort / 10)
                bar = "█" * blocks + "░" * (10 - blocks)
                self._show_result(f"Reasoning atual: {self.state.reasoning_effort}% [{bar}]\nModelo em uso: {self.state.large_model}\n\nUse Ctrl+← / Ctrl+→ para alterar em tempo de execução, ou /reasoning <0-100>.")
        elif found.id in {"autonomy", "permissions"}:
            store = getattr(self.runtime, "autonomy_store", None)
            if not arg:
                self.open_overlay("autonomy_control", self.autonomy_control)
            else:
                level = arg.strip().lower()
                preset_map = {
                    "always_allow": "balanced",
                    "always": "balanced",
                    "files_free": "balanced",
                    "1": "supervised",
                    "2": "balanced",
                    "3": "autonomous",
                    "read_only": "read_only",
                    "supervised": "supervised",
                    "balanced": "balanced",
                    "autonomous": "autonomous",
                }
                target = preset_map.get(level, level)
                try:
                    new_policy = store.set_preset(target) if store else None
                    if new_policy and hasattr(self.runtime.processor.registry, "policy"):
                        self.runtime.processor.registry.policy.autonomy = new_policy
                    if await self._ensure_daemon_management():
                        await self.bridge.set_autonomy(target)
                    self._show_result(f"Perfil de Autonomia alterado para: [{target.upper()}]")
                except ValueError as err:
                    self.state.add_toast(f"Erro de autonomia: {err}")
                    self._show_result(f"Perfil inválido: {err}")
        elif found.id in {"ask", "code"}:
            if arg:
                if self.state.is_thinking or (self.bridge and self.bridge.is_active):
                    self._show_result("A turn is already active.")
                    return True
                marker = "[QUESTION ONLY - NO CODE EDITS]" if found.id == "ask" else "[CODE EDIT REQUIRED]"
                conversation = self.runtime.history.get_or_create_active()
                try:
                    await self.bridge.start(f"{marker}: {arg}", conversation["id"], explicit_files=self.explicit_files, no_history=not self.runtime.config.history_enabled)
                except Exception as err:
                    self.state.add_toast(f"Turn Error: {err}")
            else:
                self._show_result(f"Usage: /{found.id} <prompt>")
        elif found.id == "undo":
            if await self._ensure_daemon_management():
                result = await self.bridge.undo()
                self._show_result(
                    f"Reverted changeset {result.get('changeset_id')}."
                    if result.get("reverted") else "No changeset to revert."
                )
            else:
                changeset = await self._run_blocking(self.runtime.processor.diff_applier.tracker.revert_last_changeset)
                self._show_result(f"Reverted changeset {changeset.id}." if changeset else "No changeset to revert.")
        elif found.id == "workspace":
            if not arg:
                self._show_result(str(self.runtime.canonical_root))
            else:
                await self._switch_workspace(arg)
        elif found.id == "mouse":
            self.toggle_mouse_support()
        elif found.id == "run":
            if arg:
                await self._execute_direct_tool("run_command", {"command": arg})
            else:
                self._show_result("Usage: /run <command>")
        elif found.id == "commit":
            message = arg or "Auto-commit by K.I.T.T."
            await self._execute_direct_tool("run_command", {"command": f"git commit -am {shlex.quote(message)}"})
        else:
            from kitt.ui.prime_commands import handle_prime_command
            handled = await handle_prime_command(self, found.id, arg)
            if not handled:
                self._show_result(
                    f"Command {found.aliases[0]} is registered but unavailable in this build."
                )
        if self.application:
            if not self.state.active_overlay:
                try:
                    self.application.layout.focus(self.prompt_control)
                except Exception:
                    pass
            self.application.invalidate()
        return True

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

    def _parse_model_command(self, argument: str) -> tuple[str, str, str | None, str | None]:
        role, separator, remainder = argument.partition(" ")
        aliases = {"main": "principal", "primary": "principal", "execution": "principal", "execute": "principal", "ctx": "context"}
        role = aliases.get(role.lower(), role.lower())
        provider, separator2, model = remainder.strip().partition(" ")
        valid_roles = (*self.model_setup_model.roles, "all")
        if role in valid_roles and separator and provider in self.model_setup_model.providers and separator2 and model.strip():
            model_name, _, base_url = model.strip().partition(" ")
            return role, model_name, provider, base_url.strip() or None
        if role in valid_roles and separator and remainder.strip():
            return role, remainder.strip(), None, None
        return "principal", argument.strip(), None, None

    def _role_tasks(self, role: str) -> tuple[str, tuple[str, ...]]:
        if role == "context":
            return "context", ("context-gather", "summarize")
        if role == "validation":
            return "validation", ("validate-diff",)
        return "execute", ("chat", "code-generation", "code-edit")

    def _model_for_role(self, role: str) -> str:
        router = self.runtime.processor.router
        profile_name, tasks = self._role_tasks(role)
        selected = router.config.routing.get(tasks[0], profile_name)
        profile = router.config.profiles.get(selected) or router.config.profiles.get(profile_name)
        return profile.model if profile else "unconfigured"

    def _profile_for_role(self, role: str):
        router = self.runtime.processor.router
        profile_name, tasks = self._role_tasks(role)
        selected = router.config.routing.get(tasks[0], profile_name)
        return router.config.profiles.get(selected) or router.config.profiles.get(profile_name)

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

    async def _set_model_role(self, role: str, model: str, provider: str | None = None, base_url: str | None = None) -> None:
        router = self.runtime.processor.router
        profile_name, tasks = self._role_tasks(role)
        fallback = router.config.profiles.get(profile_name)
        if fallback is None:
            fallback = router.config.profiles.get("execute") or router.config.profiles.get("context")
        if fallback is None:
            raise RuntimeError("No provider profile available")
        provider = provider or fallback.backend
        same_provider = fallback.backend == provider
        default_url, _ = self._provider_defaults(provider)
        if hasattr(self, "model_setup_model"):
            custom_entry = next((cp for cp in self.model_setup_model.custom_providers if cp["name"] == provider), None)
            if custom_entry and custom_entry.get("base_url"):
                default_url = custom_entry["base_url"]
        target_url = base_url or (fallback.base_url if fallback.backend == provider else default_url)
        if target_url and not target_url.startswith(("http://", "https://")):
            target_url = f"http://{target_url}"
        protocol = "ollama-chat" if (":11434" in (target_url or "") or "ollama" in (provider or "").lower()) else fallback.protocol
        router.config.profiles[profile_name] = replace(
            fallback, model=model, backend=provider,
            base_url=target_url,
            protocol=protocol,
            api_key=fallback.api_key if same_provider else "",
            credential_ref=(
                fallback.credential_ref if same_provider else None
            ),
            max_output_tokens=max(fallback.max_output_tokens, 2048) if role == "principal" else max(fallback.max_output_tokens, 1024),
            supports_json=provider in {"openai", "anthropic", "gemini", "deepseek", "groq", "together", "mistral", "openrouter", "antigravity", "ollama"} or "ollama" in (provider or "").lower(),
        )
        for task in tasks:
            router.config.routing[task] = profile_name
        await self._run_blocking(router.save_config, self.state.workspace_path)
        if await self._ensure_daemon_management():
            await self.bridge.reload_router()
        self._init_models_from_runtime()
        self.state.add_toast(f"{role.title()} model: {provider}/{model}")

    async def _models_for_provider(self, provider: str, base_url: str) -> list[str]:
        from kitt.llm.auth import ProviderAuthService
        from kitt.llm.catalog import ProviderCatalogService
        from kitt.llm.endpoint_security import (
            ProviderEndpointTrustStore,
            resolve_endpoint_credential,
        )
        from kitt.router.model_selector import ModelConfigurator, fetch_provider_models

        norm_url = (base_url or "").strip().rstrip("/")
        if norm_url and not norm_url.startswith(("http://", "https://")):
            norm_url = f"http://{norm_url}"

        endpoint_policy = ProviderEndpointTrustStore()
        endpoint_allowed = bool(
            norm_url
            and (
                endpoint_policy.is_trusted(provider, norm_url)
                or self._is_local_or_no_auth_provider(provider, norm_url)
            )
        )
        api_key = None
        if norm_url and endpoint_policy.is_trusted(provider, norm_url):
            try:
                api_key = resolve_endpoint_credential(
                    ProviderAuthService(),
                    provider,
                    norm_url,
                    policy=endpoint_policy,
                )
            except Exception:
                api_key = None

        # 1. Live discovery for Ollama (local, remote LAN/WAN IP, or :11434 port)
        is_ollama = (
            provider == "ollama"
            or "11434" in norm_url
            or "ollama" in provider.lower()
            or "ollama" in norm_url.lower()
        )
        if is_ollama and norm_url and endpoint_allowed:
            try:
                models = await self._run_blocking(ModelConfigurator(self.state.workspace_path).fetch_ollama_models, norm_url)
                if models:
                    return list(dict.fromkeys(models))
            except Exception:
                pass

        # 2. Live discovery for OpenAI-compatible endpoints (LM Studio, vLLM, LocalAI, custom servers, etc.)
        if norm_url and endpoint_allowed:
            try:
                models = await self._run_blocking(fetch_provider_models, provider, norm_url, api_key, 2.5)
                if models and models != [f"{provider}-default"]:
                    return list(dict.fromkeys(models))
            except Exception:
                pass

            # Also try querying OpenAI /v1/models endpoint adapter as fallback for custom servers
            try:
                models = await self._run_blocking(fetch_provider_models, "openai", norm_url, api_key, 2.5)
                if models and models != ["openai-default"]:
                    return list(dict.fromkeys(models))
            except Exception:
                pass

        # 3. Dynamic Models.dev catalog lookup
        try:
            cat = ProviderCatalogService()
            cat_models = [m.id for m in cat.models(provider)]
            if cat_models:
                return cat_models
        except Exception:
            pass

        builtin_fallbacks = {
            "openai": [
                "gpt-4o",
                "gpt-4o-mini",
                "gpt-4.1",
                "gpt-4.1-mini",
                "gpt-4.1-nano",
                "o4-mini",
            ],
            "anthropic": ["claude-3-7-sonnet", "claude-3-5-sonnet"],
            "gemini": ["gemini-1.5-pro", "gemini-1.5-flash"],
            "deepseek": ["deepseek-v3", "deepseek-r1"],
            "groq": ["llama-3.3-70b-versatile"],
            "mistral": ["mistral-large-latest"],
            "openrouter": ["openrouter/auto"],
            "antigravity": ["antigravity-chat-latest"],
        }
        if provider in builtin_fallbacks:
            return builtin_fallbacks[provider]

        return [f"{provider}-default"]

    async def _prepare_model_setup(self, base_url: str | None = None, provider: str | None = None) -> None:
        self.state.status_text = "DISCOVERING MODELS"
        profile = self._profile_for_role(self.model_setup_model.selected_role)
        norm_override = base_url.strip().rstrip("/") if base_url else None
        if norm_override and not norm_override.startswith(("http://", "https://")):
            norm_override = f"http://{norm_override}"
        self.model_setup_model.base_url_override = norm_override
        
        if provider:
            if provider in self.model_setup_model.providers:
                self.model_setup_model.provider_index = self.model_setup_model.providers.index(provider)
        elif norm_override:
            if "ollama" in self.model_setup_model.providers and ":11434" in norm_override:
                self.model_setup_model.provider_index = self.model_setup_model.providers.index("ollama")
        else:
            # First time load: sync with current role's profile backend
            if profile and profile.backend in self.model_setup_model.providers and not self.model_setup_model.models:
                self.model_setup_model.provider_index = self.model_setup_model.providers.index(profile.backend)
        
        active_provider = self.model_setup_model.selected_provider
        default_url, _ = self._provider_defaults(active_provider)

        # Check custom providers for registered base_url
        custom_entry = next((cp for cp in self.model_setup_model.custom_providers if cp["name"] == active_provider), None)
        if custom_entry and custom_entry.get("base_url"):
            default_url = custom_entry["base_url"]

        endpoint = self.model_setup_model.base_url_override or (profile.base_url if profile and profile.backend == active_provider else default_url)
        if endpoint and not endpoint.startswith(("http://", "https://")):
            endpoint = f"http://{endpoint}"
        
        models = await self._models_for_provider(active_provider, endpoint)
        self.model_setup_model.models = list(dict.fromkeys(models)) if models else ["default-model"]
        
        selected = self._model_for_role(self.model_setup_model.selected_role)
        if selected in self.model_setup_model.models:
            self.model_setup_model.model_index = self.model_setup_model.models.index(selected)
        else:
            self.model_setup_model.model_index = 0
        self.state.status_text = "SYSTEM ONLINE"
        if self.application:
            self.application.invalidate()

    def _model_setup_search_changed(self) -> None:
        self.model_setup_model.search_query = self.model_setup_search_buffer.text
        self.model_setup_model.model_index = 0
        if self.application:
            self.application.invalidate()

    async def _open_model_setup_overlay(self, base_url: str | None = None, provider: str | None = None) -> None:
        await self._prepare_model_setup(base_url, provider=provider)
        self.model_setup_search_buffer.text = ""
        self.model_setup_model.search_query = ""
        self.open_overlay("model_setup", self.model_setup_search_control)

    def _transcript_mouse_handler(self, mouse_event) -> Any:
        from prompt_toolkit.mouse_events import MouseEventType
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.state.follow_tail = False
            if hasattr(self, "transcript_window"):
                self.transcript_window.vertical_scroll = max(0, self.transcript_window.vertical_scroll - 3)
            if self.application:
                self.application.invalidate()
            return None
        elif mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            if hasattr(self, "transcript_window"):
                self.transcript_window.vertical_scroll += 3
            if self.application:
                self.application.invalidate()
            return None
        return NotImplemented

    def toggle_mouse_support(self) -> bool:
        self.mouse_support_enabled = not getattr(self, "mouse_support_enabled", True)
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

    def _select_popup_action(self, entry: dict) -> None:
        self.close_overlay()
        aname = entry.get("name", "")
        if aname == "add_provider_ollama":
            self._open_add_provider_overlay("ollama")
        elif aname == "add_provider_openai":
            self._open_add_provider_overlay("openai")
        elif aname.startswith("edit_provider_"):
            target = entry.get("target_provider", aname.replace("edit_provider_", ""))
            self._open_edit_provider_overlay(target)
        elif aname.startswith("delete_provider_"):
            target = entry.get("target_provider", aname.replace("delete_provider_", ""))
            asyncio.create_task(self._delete_custom_provider(target))
        else:
            self._open_add_provider_overlay()

    def _open_provider_popup_overlay(self) -> None:
        self.open_overlay("provider_popup", self.provider_popup_control)

    def _provider_popup_text(self) -> str:
        setup = self.model_setup_model
        entries = setup.get_popup_entries()
        total = len(entries)
        lines = [
            f"Menu de Provedores ({total} opções)  (Espaço/F: Favorito ★ | A/+: Novo | E: Editar | D: Excluir | Enter: Selecionar | Esc: Fechar)\n"
        ]
        window_size = 25
        start = min(max(0, setup.provider_popup_index - (window_size // 2)), max(0, total - window_size))
        end = min(total, start + window_size)

        if start > 0:
            lines.append(f"  ▲ ... ({start} opções acima)\n")

        from kitt.llm.auth import ProviderAuthService
        auth_service = ProviderAuthService()

        for idx in range(start, end):
            entry = entries[idx]
            if entry["kind"] == "header":
                lines.append(f"\n {entry['title']}")
            elif entry["kind"] == "provider":
                cursor = ">" if idx == setup.provider_popup_index else " "
                star = "★" if entry["is_favorite"] else "☆"
                pname = entry["name"]
                is_current = " [ativo]" if pname == setup.selected_provider else ""
                if self._is_local_or_no_auth_provider(pname):
                    status_glyph = "◌ local / sem auth"
                elif bool(auth_service.resolve(None, pname)):
                    status_glyph = "● conectado"
                else:
                    status_glyph = "○ não autenticado"
                lines.append(f" {cursor} {star} {pname:<16} │ {status_glyph:<18}{is_current}")
            elif entry["kind"] == "action":
                cursor = ">" if idx == setup.provider_popup_index else " "
                lines.append(f" {cursor} {entry['title']}")

        if end < total:
            lines.append(f"\n  ▼ ... ({total - end} opções abaixo)")
        return "\n".join(lines)

    async def _persist_custom_providers(self) -> None:
        try:
            router = getattr(self.runtime.processor, "router", None)
            if router and hasattr(router, "config") and router.config:
                router.config.custom_providers = list(self.model_setup_model.custom_providers)
                if hasattr(self.runtime.processor, "registry"):
                    for cp in self.model_setup_model.custom_providers:
                        self.runtime.processor.registry.register_custom_provider(
                            provider_id=cp.get("name", ""),
                            name=cp.get("name", ""),
                            protocol=cp.get("protocol", "openai-chat-completions"),
                            base_url=cp.get("base_url", ""),
                        )
                await self._run_blocking(router.save_config, self.state.workspace_path)
                if await self._ensure_daemon_management():
                    await self.bridge.reload_router()
        except Exception as err:
            self.state.add_toast(f"Aviso: falha ao persistir provedores: {err}")

    def _open_add_provider_overlay(self, preset: str | None = None) -> None:
        self.editing_provider_name = None
        if preset:
            self.model_setup_model.set_pattern_by_id(preset)
        pat = self.model_setup_model.selected_pattern
        self.add_provider_name_buffer.text = ""
        self.add_provider_url_buffer.text = pat.get("default_url", "http://")
        self.open_overlay("add_provider", self.add_provider_name_control)

    def _open_edit_provider_overlay(self, name: str) -> None:
        self.editing_provider_name = name
        cp = self.model_setup_model.get_custom_provider(name)
        if not cp:
            self.state.add_toast(f"Provedor customizado '{name}' não encontrado para edição.", persistent=True)
            return

        proto = cp.get("protocol", "openai-chat-completions")
        bkend = cp.get("backend", "openai")
        if "ollama" in proto or "ollama" in bkend:
            self.model_setup_model.set_pattern_by_id("ollama")
        elif "anthropic" in proto or "anthropic" in bkend:
            self.model_setup_model.set_pattern_by_id("anthropic")
        elif "gemini" in proto or "gemini" in bkend:
            self.model_setup_model.set_pattern_by_id("gemini")
        else:
            self.model_setup_model.set_pattern_by_id("openai")

        self.add_provider_name_buffer.text = cp.get("name", name)
        self.add_provider_url_buffer.text = cp.get("base_url", "http://")
        self.open_overlay("add_provider", self.add_provider_url_control)

    async def _delete_custom_provider(self, name: str) -> None:
        deleted = self.model_setup_model.delete_custom_provider(name)
        if deleted:
            await self._persist_custom_providers()
            self.state.add_toast(f"✓ Provedor '{name}' removido e configuração atualizada!", persistent=False)
            await self._prepare_model_setup()
        else:
            self.state.add_toast(f"Provedor '{name}' não encontrado para exclusão.", persistent=True)
        if self.application:
            self.application.invalidate()

    def _add_provider_help_text(self) -> str:
        pat = self.model_setup_model.selected_pattern
        header = f"Editar Provedor Customizado '{self.editing_provider_name}'" if self.editing_provider_name else "Cadastrar Novo Provedor Customizado"
        return (
            f"{header}\n"
            f"Padrão / Protocolo: ◄ {pat['label']} ►  ([P] ou [Ctrl+←/→] para alterar padrão)\n"
            f"[Tab] Alternar Nome/URL  |  [Enter] Salvar e Descobrir  |  [Esc] Cancelar\n"
        )

    def _accept_add_provider(self, buffer) -> bool:
        name = self.add_provider_name_buffer.text.strip().lower()
        url = buffer.text.strip().rstrip("/")
        if not name:
            self.state.add_toast("Nome do provedor não pode ser vazio.", persistent=True)
            return False
        from kitt.llm.endpoint_security import is_reserved_provider_id
        if is_reserved_provider_id(name):
            self.state.add_toast(
                f"'{name}' é um ID reservado de provedor built-in. Use um nome customizado único.",
                persistent=True,
            )
            return False
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        pat = self.model_setup_model.selected_pattern

        if self.editing_provider_name:
            self.model_setup_model.edit_custom_provider(
                name=self.editing_provider_name,
                new_name=name,
                base_url=url,
                backend=pat.get("default_backend", "openai"),
                protocol=pat.get("protocol", "openai-chat-completions"),
            )
        else:
            self.model_setup_model.add_custom_provider(
                name=name,
                base_url=url,
                backend=pat.get("default_backend", "openai"),
                protocol=pat.get("protocol", "openai-chat-completions"),
            )
        self.close_overlay()
        asyncio.get_running_loop().create_task(self._finish_add_provider(name, url))
        return True

    async def _finish_add_provider(self, name: str, url: str) -> None:
        from kitt.llm.endpoint_security import ProviderEndpointTrustStore
        ProviderEndpointTrustStore().trust(name, url)
        await self._persist_custom_providers()
        await self._prepare_model_setup(base_url=url, provider=name)
        action_msg = "atualizado" if self.editing_provider_name else "adicionado"
        self.state.add_toast(f"✓ Provedor '{name}' {action_msg} e persistido com sucesso!", persistent=False)
        self.editing_provider_name = None
        if self.application:
            self.application.invalidate()

    def _open_provider_endpoint_overlay(self) -> None:
        profile = self._profile_for_role(self.model_setup_model.selected_role)
        self.provider_endpoint_buffer.text = self.model_setup_model.base_url_override or (profile.base_url if profile and profile.backend == "ollama" else "http://")
        self.open_overlay("provider_endpoint", self.provider_endpoint_control)

    @staticmethod
    def _provider_endpoint_text():
        return "Informe a URL do endpoint remoto (ex: http://192.168.1.50:11434):\n[Enter] Descobrir Modelos  |  [Esc] Cancelar\n"

    async def _submit_provider_endpoint(self, endpoint: str) -> None:
        endpoint = endpoint.strip().rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            self.state.add_toast("Endpoint must start with http:// or https://", persistent=True)
            return
        from kitt.llm.endpoint_security import ProviderEndpointTrustStore
        ProviderEndpointTrustStore().trust(
            self.model_setup_model.selected_provider,
            endpoint,
        )
        self.close_overlay()
        await self._prepare_model_setup(endpoint)
        if self.application:
            self.application.invalidate()

    def _auth_login_help_text(self) -> str:
        from kitt.llm.auth import ProviderAuthService
        prov = self.target_auth_provider or "Provedor"
        env_var = ProviderAuthService.get_default_env_var(prov)
        env_val = ProviderAuthService.get_env_value(env_var)

        env_status = ""
        if env_val:
            masked = env_val[:4] + "..." + env_val[-3:] if len(env_val) > 8 else "***"
            env_status = (
                f"  ✓ Variável ${env_var} detectada no ambiente / .env ({masked})\n"
                f"  ★ Pressione [Enter com campo vazio] ou digite 'env' para usar ${env_var}\n"
            )
        else:
            env_status = (
                f"  • Variável de ambiente: export {env_var}=\"sua_chave\"\n"
                f"    (ou adicione '{env_var}=sua_chave' no arquivo .env do projeto)\n"
            )

        return (
            f"Autenticação do Provedor: {prov.upper()}\n\n"
            f"{env_status}\n"
            f"Ou digite a API Key / Secret abaixo (Salva em ~/.kitt/auth.json [0600]):\n"
            "[Enter] Salvar Credencial  |  [Esc] Pular/Cancelar\n"
        )

    async def _start_oauth_flow(self, provider: str) -> None:
        from kitt.llm.auth import ProviderAuthService
        from kitt.llm.oauth import OAuthManager
        mgr = OAuthManager()
        if not mgr.is_oauth_supported(provider):
            self.state.add_toast(f"OAuth não suportado para {provider}. Digite a API key.", persistent=False)
            return

        cfg = mgr.get_config(provider)
        if not cfg:
            return

        auth_service = ProviderAuthService()

        if cfg.flow_type == "device_code":
            try:
                challenge = await self._run_blocking(mgr.start_device_code_flow, provider)
                self.state.add_toast(
                    f"🔑 Acesse {challenge.verification_uri} e digite o código: {challenge.user_code}",
                    persistent=True,
                )
                try:
                    import webbrowser
                    webbrowser.open(challenge.verification_uri)
                except Exception:
                    pass
                token = await self._run_blocking(mgr.poll_device_code_token, provider, challenge, 180.0)
                auth_service.login_oauth(provider, token)
                self.state.add_toast(f"✓ Conectado via OAuth com sucesso ({provider})!", persistent=False)
                self.close_overlay()
                if self.pending_model_selection:
                    role, model, prov, base_url = self.pending_model_selection
                    self.pending_model_selection = None
                    await self._apply_pending_model(role, model, prov, base_url)
            except Exception as exc:
                self.state.add_toast(f"OAuth falhou: {exc}", persistent=True)
        else:
            try:
                auth_url, server, verifier, state = await self._run_blocking(mgr.start_browser_flow, provider, True)
                self.state.add_toast(f"⏳ Navegador aberto. Ou acesse: {auth_url}", persistent=True)

                res = await self._run_blocking(server.wait_for_callback, 120.0)
                server.stop()
                received_state = res.get("state", "")
                from kitt.llm.oauth import validate_state
                if not validate_state(state, received_state):
                    self.state.add_toast("Erro de segurança OAuth: Validação de state falhou (CSRF).", persistent=True)
                    return

                code = res.get("code")
                if not code:
                    self.state.add_toast("Código de autorização não recebido", persistent=True)
                    return

                redirect_uri = f"http://127.0.0.1:{server.port}/callback"
                token = await self._run_blocking(mgr.exchange_code_for_token, provider, code, verifier, redirect_uri)
                auth_service.login_oauth(provider, token)
                self.state.add_toast(f"✓ Conectado via OAuth com sucesso ({provider})!", persistent=False)
                self.close_overlay()
                if self.pending_model_selection:
                    role, model, prov, base_url = self.pending_model_selection
                    self.pending_model_selection = None
                    await self._apply_pending_model(role, model, prov, base_url)
            except Exception as exc:
                self.state.add_toast(f"OAuth falhou: {exc}", persistent=True)
        if self.application:
            self.application.invalidate()

    def _accept_model_setup_search(self, buffer) -> bool:
        asyncio.create_task(self._apply_selected_model())
        return True

    def _open_auth_login_overlay(self, provider: str, parent_name: str | None = None) -> None:
        self.target_auth_provider = provider.strip().lower()
        self.auth_login_buffer.text = ""
        self.open_overlay("auth_login", self.auth_login_control, parent_name=parent_name)

    def _accept_auth_login(self, buffer) -> bool:
        key = buffer.text.strip()
        prov = self.target_auth_provider or "openai"
        from kitt.llm.auth import ProviderAuthService
        auth_service = ProviderAuthService()
        env_var = auth_service.get_default_env_var(prov)
        env_val = auth_service.get_env_value(env_var)

        def _trust_pending_endpoint() -> None:
            from kitt.llm.endpoint_security import ProviderEndpointTrustStore
            endpoint = None
            if self.pending_model_selection:
                _, _, pending_provider, pending_url = self.pending_model_selection
                if pending_provider == prov:
                    endpoint = pending_url
            if not endpoint and hasattr(self, "model_setup_model"):
                custom = next(
                    (
                        cp for cp in self.model_setup_model.custom_providers
                        if cp.get("name") == prov
                    ),
                    None,
                )
                if custom:
                    endpoint = custom.get("base_url")
            if endpoint:
                ProviderEndpointTrustStore().trust(prov, endpoint)

        if key.lower() in ("e", "env", "use_env", "$env"):
            if env_val:
                _trust_pending_endpoint()
                auth_service.login(prov, f"env:{env_var}", method="env")
                self.state.add_toast(f"✓ Conectado via variável de ambiente (${env_var})!", persistent=False)
                self.close_overlay()
                if self.pending_model_selection:
                    role, model, provider, base_url = self.pending_model_selection
                    self.pending_model_selection = None
                    asyncio.create_task(self._apply_pending_model(role, model, provider, base_url))
                if self.application:
                    self.application.invalidate()
                return True
            else:
                self.state.add_toast(f"Variável ${env_var} não encontrada no ambiente ou .env. Digite a API Key.", persistent=True)
                return False

        if not key:
            if env_val:
                _trust_pending_endpoint()
                auth_service.login(prov, f"env:{env_var}", method="env")
                self.state.add_toast(f"✓ Conectado via variável de ambiente (${env_var})!", persistent=False)
                self.close_overlay()
                if self.pending_model_selection:
                    role, model, provider, base_url = self.pending_model_selection
                    self.pending_model_selection = None
                    asyncio.create_task(self._apply_pending_model(role, model, provider, base_url))
                if self.application:
                    self.application.invalidate()
                return True
            elif self._is_local_or_no_auth_provider(prov):
                auth_service.login(prov, "", method="none")
                self.state.add_toast(f"✓ Provedor '{prov}' configurado sem token.", persistent=False)
                self.close_overlay()
                if self.pending_model_selection:
                    role, model, provider, base_url = self.pending_model_selection
                    self.pending_model_selection = None
                    asyncio.create_task(self._apply_pending_model(role, model, provider, base_url))
                if self.application:
                    self.application.invalidate()
                return True
            else:
                self.state.add_toast(f"Autenticação de {prov} cancelada.", persistent=False)
                self.pending_model_selection = None
                self.close_overlay()
                return True

        _trust_pending_endpoint()
        auth_service.login(prov, key, method="api_key")
        self.state.add_toast(f"✓ Credenciais salvas com segurança para {prov}!", persistent=False)
        self.close_overlay()

        if self.pending_model_selection:
            role, model, provider, base_url = self.pending_model_selection
            self.pending_model_selection = None
            asyncio.create_task(self._apply_pending_model(role, model, provider, base_url))

        if self.application:
            self.application.invalidate()
        return True

    async def _apply_pending_model(self, role: str, model: str, provider: str, base_url: str | None) -> None:
        try:
            await self._set_model_role(role, model, provider, base_url)
            self.state.add_toast(f"✓ Cargo '{role.title()}' definido: {provider}/{model} (Esc para fechar)", duration=3.5)
        except Exception as exc:
            self.state.add_toast(f"Falha ao atribuir modelo: {exc}", persistent=True)
        if self.application:
            self.application.invalidate()

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
            if err:
                return f" ✖ [FALHA NO PROCESSO] {len(err)} tarefa(s) com erro | {len(done)} concluída(s)"
            return f" ✔ [PROCESSO CONCLUÍDO] {len(done)} tarefa(s)/agente(s) finalizados com sucesso!"

    def _is_local_or_no_auth_provider(self, provider: str, url: str = "") -> bool:
        p = (provider or "").strip().lower()
        u = (url or "").strip().lower()
        if p in ("ollama", "lmstudio", "custom") or "ollama" in p or "lmstudio" in p:
            return True
        if hasattr(self, "model_setup_model") and any(cp["name"] == p for cp in self.model_setup_model.custom_providers):
            return True
        lan_markers = (
            ":11434", ":1234", ":8000", ":8080", ":5000",
            "localhost", "127.0.0.1", "192.168.", "10.",
            "172.16.", "172.17.", "172.18.", "172.19.", "172.20.",
            "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
            "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",
            ".local", ".lan", ".internal"
        )
        if any(marker in u for marker in lan_markers):
            return True
        return False

    async def _apply_selected_model(self) -> None:
        model = self.model_setup_model.selected_model
        if not model:
            self.state.add_toast("Nenhum modelo selecionado", persistent=True)
            return
        role = self.model_setup_model.selected_role
        provider = self.model_setup_model.selected_provider
        profile = self._profile_for_role(role)
        base_url = self.model_setup_model.base_url_override or (profile.base_url if profile and profile.backend == provider else self._provider_defaults(provider)[0])

        from kitt.llm.auth import ProviderAuthService
        auth_service = ProviderAuthService()
        auth_state = auth_service.state(provider)
        is_auth = (
            (auth_state.auth_type == "none")
            or bool(auth_service.resolve(auth_state.credential_ref, provider))
            or self._is_local_or_no_auth_provider(provider, base_url)
        )

        if not is_auth:
            self.pending_model_selection = (role, model, provider, base_url)
            self._open_auth_login_overlay(provider, parent_name="model_setup")
            if self.application:
                self.application.invalidate()
            return

        try:
            await self._set_model_role(role, model, provider, base_url)
            self.state.add_toast(f"✓ Cargo '{role.title()}' definido: {provider}/{model} (Esc para fechar)", duration=3.5)
        except Exception as exc:
            self.state.add_toast(f"Falha ao atualizar modelo: {exc}", persistent=True)
        if self.application:
            self.application.invalidate()

    def _show_result(self, text: str) -> None:
        self.state.route = "session"
        self.state.append_message("system", safe_text(text)[:12000])
        if self.application:
            if not self.state.active_overlay:
                try:
                    self.application.layout.focus(self.prompt_control)
                except Exception:
                    pass
            self.application.invalidate()

    async def _show_history(self, search: str = "") -> None:
        conversations = await self._run_blocking(self.runtime.history.list_history, 20, 0, search or None)
        active = self.runtime.history.active_conversation
        active_id = active.get("id") if active else None
        self._show_result("\n".join(
            f"{'*' if c['id'] == active_id else ' '} {idx}. {c['id'][:8]} | {c['title']}"
            for idx, c in enumerate(conversations, 1)
        ) or "No conversations.")

    async def _show_active_history(self) -> None:
        conversation = self.runtime.history.get_or_create_active()
        messages = await self._run_blocking(self.runtime.history.repo.get_messages_for_conversation, conversation["id"])
        self._show_result("\n\n".join(f"{message['role'].upper()}: {message['content']}" for message in messages) or "No messages in active conversation.")

    async def _load_conversation(self, conversation: dict) -> None:
        messages = await self._run_blocking(self.runtime.history.repo.get_messages_for_conversation, conversation["id"])
        self.state.active_conversation_id = conversation["id"]
        self.state.route = "session"
        self.state.transcript.clear()
        for message in messages[-500:]:
            self.state.append_message(message.get("role", "system"), message.get("content", ""))

    async def _execute_direct_tool(self, tool_name: str, args: dict) -> None:
        conversation = self.runtime.history.get_or_create_active()
        self.state.active_conversation_id = conversation["id"]
        if await self._ensure_daemon_management():
            try:
                response = await self.bridge.execute_direct_tool(tool_name, args)
            except Exception as exc:
                self._show_result(f"Error: {exc}")
                return
            if response.get("requires_approval"):
                # ApprovalRequired is journaled and delivered by daemon exactly once.
                self.state.status_text = "APPROVAL"
                return
            self._show_result(
                str(response.get("output") or "")
                if response.get("success", False)
                else f"Error: {response.get('error') or response.get('output') or 'tool failed'}"
            )
            return

        turn_id = f"ui-{uuid.uuid4().hex[:12]}"
        workspace_id = self.runtime.workspace_id
        result = await self._run_blocking(
            self.runtime.registry.execute_tool, tool_name, args,
            turn_id, conversation["id"], workspace_id,
        )
        if result.requires_approval:
            action_hash = self.runtime.policy.generate_action_hash(tool_name, args)
            approval_id = f"req_{turn_id}_{action_hash[:8]}"
            self.runtime.approval.register_request(
                turn_id, conversation["id"], workspace_id, action_hash, approval_id, tool_name=tool_name,
                summary=f"{tool_name}: {safe_text(args)}",
            )
            self._on_event(ApprovalRequired(
                turn_id=turn_id, conversation_id=conversation["id"], tool_name=tool_name, args=args, action_hash=action_hash,
                approval_request_id=approval_id, workspace_id=workspace_id,
            ))
            pending = self.state.pending_approval
            if pending:
                pending["direct_tool"] = True
            return
        self._show_result(result.output if result.success else f"Error: {result.error or result.output}")

    async def _switch_workspace(self, raw_path: str) -> None:
        target = Path(raw_path).expanduser().resolve()
        if self.state.is_thinking:
            self._show_result("Cannot switch workspace while a turn is running.")
            return
        if not target.is_dir():
            self._show_result(f"Directory not found: {raw_path}")
            return
        try:
            new_runtime = await self.runtime.aswitch_workspace(str(target))
        except Exception as exc:
            self._show_result(f"Workspace switch failed: {exc}")
            return
        self.runtime = new_runtime
        self.state.workspace_path = str(new_runtime.canonical_root)
        self.state.workspace_name = new_runtime.canonical_root.name or str(new_runtime.canonical_root)
        self.state.current_branch = read_git_branch_name(new_runtime.canonical_root)
        self.state.active_conversation_id = None
        self.state.transcript.clear()
        self.explicit_files.clear()
        self._init_models_from_runtime()
        self.session_picker_model = SessionPickerModel(new_runtime)
        self.timeline_model = TimelineModel(new_runtime)
        self.diff_model = DiffViewerModel(str(new_runtime.canonical_root))
        if self.application:
            self.bridge = TurnEventBridge(new_runtime, self._on_event, self.application.invalidate)
        self._show_result(f"Switched workspace: {new_runtime.canonical_root}")

    async def resolve_approval(self, mode: str | bool = "once") -> None:
        pending = self.state.pending_approval
        if not pending:
            return

        remember_scope = None
        if mode is True or mode == "once":
            allow = True
        elif mode is False or mode == "deny":
            allow = False
        elif mode in {"always_workspace", "always", "A"}:
            allow = True
            remember_scope = "workspace"
        elif mode in {"always_session", "session", "s"}:
            allow = True
            remember_scope = "session"
        elif mode == "deny_all":
            if self.bridge and self.bridge.daemon_mode:
                for req in list(self.state.pending_approvals):
                    try:
                        await self.bridge.resolve_approval(req["approval_id"], False)
                    except Exception:
                        pass
            else:
                for req in list(self.state.pending_approvals):
                    try:
                        self.runtime.approval.deny(req["approval_id"], "Denied all in queue")
                    except Exception:
                        pass
            self.state.pending_approvals.clear()
            self.close_overlay()
            if self.bridge and self.bridge.is_active:
                await self.bridge.cancel("Denied all in queue")
            if self.application:
                self.application.invalidate()
            return
        else:
            allow = bool(mode)

        self.state.status_text = "APPROVING" if allow else "DENYING"
        if self.bridge and self.bridge.daemon_mode:
            try:
                tool_name = pending.get("tool_name", "apply_patch")
                if remember_scope:
                    await self.bridge.remember_approval(tool_name, remember_scope)
                    if remember_scope == "workspace":
                        await self.bridge.set_autonomy("balanced")
                    self.state.add_toast(
                        f"Sempre permitir {tool_name} ativado para este {remember_scope}."
                    )
                await self.bridge.resolve_approval(pending["approval_id"], allow)
                if self.state.pending_approvals:
                    self.state.pending_approvals.pop(0)
                self.close_overlay()
                self.state.status_text = "PROCESSING" if allow else "SYSTEM ONLINE"
            except Exception as exc:
                self.state.add_toast(f"Approval failed: {exc}", persistent=True)
                self.state.status_text = "ERROR"
            if self.application:
                self.application.invalidate()
            return

        if remember_scope:
            tool_name = pending.get("tool_name", "apply_patch")
            self.runtime.approval.remember(tool_name, "**", "allow", remember_scope)
            if remember_scope == "workspace":
                self.runtime.autonomy_store.set_preset("balanced")
                self.runtime.processor.registry.policy.autonomy = self.runtime.autonomy_store.get()
            self.state.add_toast(f"Sempre permitir {tool_name} ativado para este {remember_scope}.")

        if allow:
            try:
                grant = self.runtime.approval.issue_grant(
                    pending["turn_id"], pending["conversation_id"], pending["workspace_id"], pending["action_hash"],
                    approval_id=pending["approval_id"],
                )
                self.state.pending_approvals.pop(0)
                self.close_overlay()
                if pending.get("direct_tool"):
                    result = await self._run_blocking(
                        self.runtime.registry.execute_tool,
                        pending["tool_name"], pending["args"], pending["turn_id"], pending["conversation_id"],
                        pending["workspace_id"], None, grant, pending["approval_id"], "USER",
                    )
                    self._show_result(result.output if result.success else f"Error: {result.error or result.output}")
                    self.state.status_text = "SYSTEM ONLINE"
                else:
                    await self.bridge.continue_turn(pending["turn_id"], grant)
            except Exception as exc:
                self.state.add_toast(f"Approval failed: {exc}", persistent=True)
                self.state.status_text = "ERROR"
        else:
            try:
                self.runtime.approval.deny(pending["approval_id"], "Approval denied")
            except Exception:
                pass
            self.state.pending_approvals.pop(0)
            self.close_overlay()
            if pending.get("direct_tool"):
                self._show_result("Command denied.")
                self.state.status_text = "SYSTEM ONLINE"
            else:
                await self.bridge.cancel("Approval denied")
        if self.application:
            self.application.invalidate()

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
        if self.application and not self.application.is_done:
            self.application.exit(result=0)

    def _key_bindings(self):
        from prompt_toolkit.filters import Condition
        from prompt_toolkit.key_binding import KeyBindings
        kb = KeyBindings()

        permission = Condition(lambda: self.state.active_overlay == "permission")
        palette = Condition(lambda: self.state.active_overlay == "palette")
        session_picker = Condition(lambda: self.state.active_overlay == "session_picker")
        timeline = Condition(lambda: self.state.active_overlay == "timeline")
        diff_overlay = Condition(lambda: self.state.active_overlay == "diff")
        model_setup = Condition(lambda: self.state.active_overlay == "model_setup")
        provider_popup = Condition(lambda: self.state.active_overlay == "provider_popup")
        add_provider = Condition(lambda: self.state.active_overlay == "add_provider")
        provider_endpoint = Condition(lambda: self.state.active_overlay == "provider_endpoint")
        auth_login = Condition(lambda: self.state.active_overlay == "auth_login")
        editor_focused = Condition(lambda: self.application and self.application.layout.current_control is self.prompt_control)

        @kb.add("tab", filter=auth_login)
        def _(event):
            prov = self.target_auth_provider or "openai"
            from kitt.llm.auth import ProviderAuthService
            env_var = ProviderAuthService.get_default_env_var(prov)
            if ProviderAuthService.get_env_value(env_var):
                self.auth_login_buffer.text = "env"
                event.app.invalidate()
        can_submit = Condition(
            lambda: bool(self.prompt_buffer.text.strip()) and (
                self.prompt_buffer.text.strip().startswith("/")
                or (not self.state.is_thinking and not (self.bridge and self.bridge.is_active))
            )
        )

        @kb.add("enter", filter=editor_focused & can_submit)
        def submit_prompt(event):
            event.current_buffer.validate_and_handle()

        @kb.add("enter", filter=editor_focused & ~can_submit)
        def submit_blocked_feedback(event):
            if self.state.is_thinking or (self.bridge and self.bridge.is_active):
                self.state.add_toast("Execução em andamento. Pressione Ctrl+C para cancelar antes de enviar novo comando.", duration=3.0)
                if self.application:
                    self.application.invalidate()

        @kb.add("escape", "enter", filter=editor_focused)
        def insert_newline(event):
            event.current_buffer.insert_text("\n")

        @kb.add("f1")
        @kb.add("?", filter=~editor_focused)
        def _(event):
            self.open_overlay("help", self.help_control)

        @kb.add("c-p", filter=~palette)
        def _(event): self.open_overlay("palette", self.palette_search_control)

        @kb.add("c-n", filter=palette)
        @kb.add("down", filter=palette)
        def _(event): self._move_palette(1)

        @kb.add("c-p", filter=palette)
        @kb.add("up", filter=palette)
        def _(event): self._move_palette(-1)

        @kb.add("c-x", "n")
        def _(event): self._new_conversation()

        @kb.add("c-x", "b")
        def _(event): self._toggle_sidebar()

        @kb.add("c-x", "l")
        def _(event): asyncio.create_task(self._open_session_picker_overlay())

        @kb.add("c-x", "g")
        def _(event): asyncio.create_task(self._open_timeline_overlay())

        @kb.add("c-x", "d")
        def _(event): asyncio.create_task(self._open_diff_overlay())

        @kb.add("c-x", "m")
        def _(event): asyncio.create_task(self._open_model_setup_overlay())

        @kb.add("c-x", "a")
        @kb.add("c-x", "c-a")
        def _(event): self.open_overlay("agents", self.agents_control)

        @kb.add("c-x", "s")
        def _(event): self.state.add_toast(self._status_text()); event.app.invalidate()

        @kb.add("c-x", "c")
        def _(event): self.state.add_toast(self._context_details_text(), persistent=True); event.app.invalidate()

        @kb.add("c-o", filter=~palette & ~auth_login & ~model_setup)
        def _(event): self.state.toggle_last_tool_collapse(); event.app.invalidate()

        @kb.add("c-right", filter=~model_setup & ~palette)
        @kb.add("c-x", "right")
        def increase_reasoning(event):
            self.state.reasoning_effort = min(100, self.state.reasoning_effort + 10)
            if hasattr(self.runtime, "processor"):
                self.runtime.processor.reasoning_effort = self.state.reasoning_effort
            if self.bridge and self.bridge.daemon_mode:
                asyncio.create_task(self.bridge.set_reasoning(self.state.reasoning_effort))
            blocks = int(self.state.reasoning_effort / 10)
            bar = "█" * blocks + "░" * (10 - blocks)
            model_name = self.state.large_model or "execution"
            self.state.add_toast(f"🧠 Reasoning: {self.state.reasoning_effort}% [{bar}] ({model_name})", duration=2.0)
            if self.application:
                self.application.invalidate()

        @kb.add("c-left", filter=~model_setup & ~palette)
        @kb.add("c-x", "left")
        def decrease_reasoning(event):
            self.state.reasoning_effort = max(0, self.state.reasoning_effort - 10)
            if hasattr(self.runtime, "processor"):
                self.runtime.processor.reasoning_effort = self.state.reasoning_effort
            if self.bridge and self.bridge.daemon_mode:
                asyncio.create_task(self.bridge.set_reasoning(self.state.reasoning_effort))
            blocks = int(self.state.reasoning_effort / 10)
            bar = "█" * blocks + "░" * (10 - blocks)
            model_name = self.state.large_model or "execution"
            self.state.add_toast(f"🧠 Reasoning: {self.state.reasoning_effort}% [{bar}] ({model_name})", duration=2.0)
            if self.application:
                self.application.invalidate()

        @kb.add("down", filter=session_picker)
        @kb.add("c-n", filter=session_picker)
        def _(event):
            self.session_picker_model.move_selection(1)
            event.app.invalidate()

        @kb.add("up", filter=session_picker)
        @kb.add("c-p", filter=session_picker)
        def _(event):
            self.session_picker_model.move_selection(-1)
            event.app.invalidate()

        @kb.add("enter", filter=session_picker)
        def _(event):
            sel = self.session_picker_model.get_selected()
            if sel:
                self.close_overlay()
                asyncio.create_task(self._execute_command(f"/resume {sel['id']}"))

        @kb.add("down", filter=timeline)
        @kb.add("c-n", filter=timeline)
        def _(event):
            self.timeline_model.move_selection(1)
            event.app.invalidate()

        @kb.add("up", filter=timeline)
        @kb.add("c-p", filter=timeline)
        def _(event):
            self.timeline_model.move_selection(-1)
            event.app.invalidate()

        @kb.add("down", filter=diff_overlay)
        def _(event):
            self.diff_model.scroll(1)
            event.app.invalidate()

        @kb.add("up", filter=diff_overlay)
        def _(event):
            self.diff_model.scroll(-1)
            event.app.invalidate()

        @kb.add("down", filter=model_setup)
        @kb.add("c-n", filter=model_setup)
        def _(event):
            self.model_setup_model.move_model(1)
            event.app.invalidate()

        @kb.add("up", filter=model_setup)
        @kb.add("c-p", filter=model_setup)
        def _(event):
            self.model_setup_model.move_model(-1)
            event.app.invalidate()

        @kb.add("p", filter=model_setup)
        @kb.add("P", filter=model_setup)
        @kb.add("space", filter=model_setup)
        def _(event):
            self._open_provider_popup_overlay()

        @kb.add("l", filter=model_setup)
        @kb.add("L", filter=model_setup)
        def _(event):
            prov = self.model_setup_model.selected_provider
            self._open_auth_login_overlay(prov, parent_name="model_setup")

        @kb.add("a", filter=model_setup)
        @kb.add("A", filter=model_setup)
        @kb.add("+", filter=model_setup)
        def _(event):
            self._open_add_provider_overlay()

        @kb.add("tab", filter=model_setup)
        def _(event):
            asyncio.create_task(self._move_model_role(1))

        @kb.add("s-tab", filter=model_setup)
        def _(event):
            asyncio.create_task(self._move_model_provider(-1))

        @kb.add("c-right", filter=model_setup)
        def _(event): asyncio.create_task(self._move_model_provider(1))

        @kb.add("c-left", filter=model_setup)
        def _(event): asyncio.create_task(self._move_model_provider(-1))

        @kb.add("escape", "c-@", filter=model_setup)
        @kb.add("c-x", "a", filter=model_setup)
        def _(event): self._open_provider_endpoint_overlay()

        @kb.add("enter", filter=model_setup)
        def _(event): asyncio.create_task(self._apply_selected_model())

        # Provider Popup Dropdown keybindings
        @kb.add("down", filter=provider_popup)
        @kb.add("c-n", filter=provider_popup)
        def _(event):
            self.model_setup_model.move_popup_selection(1)
            event.app.invalidate()

        @kb.add("up", filter=provider_popup)
        @kb.add("c-p", filter=provider_popup)
        def _(event):
            self.model_setup_model.move_popup_selection(-1)
            event.app.invalidate()

        @kb.add("f", filter=provider_popup)
        @kb.add("F", filter=provider_popup)
        @kb.add("space", filter=provider_popup)
        def _(event):
            entry = self.model_setup_model.get_selected_popup_entry()
            if entry and entry["kind"] == "provider":
                is_fav = self.model_setup_model.toggle_favorite(entry["name"])
                tag = "adicionado aos favoritos ★" if is_fav else "removido dos favoritos ☆"
                self.state.add_toast(f"Provedor '{entry['name']}': {tag}", persistent=False)
                event.app.invalidate()

        @kb.add("a", filter=provider_popup)
        @kb.add("A", filter=provider_popup)
        @kb.add("+", filter=provider_popup)
        def _(event):
            self._open_add_provider_overlay()

        @kb.add("e", filter=provider_popup)
        @kb.add("E", filter=provider_popup)
        def _(event):
            entry = self.model_setup_model.get_selected_popup_entry()
            if entry:
                if entry["kind"] == "provider" and self.model_setup_model.get_custom_provider(entry["name"]):
                    self.close_overlay()
                    self._open_edit_provider_overlay(entry["name"])
                elif entry["kind"] == "action" and entry.get("name", "").startswith("edit_provider_"):
                    self._select_popup_action(entry)

        @kb.add("d", filter=provider_popup)
        @kb.add("D", filter=provider_popup)
        @kb.add("delete", filter=provider_popup)
        def _(event):
            entry = self.model_setup_model.get_selected_popup_entry()
            if entry:
                if entry["kind"] == "provider" and self.model_setup_model.get_custom_provider(entry["name"]):
                    self.close_overlay()
                    asyncio.create_task(self._delete_custom_provider(entry["name"]))
                elif entry["kind"] == "action" and entry.get("name", "").startswith("delete_provider_"):
                    self._select_popup_action(entry)

        @kb.add("enter", filter=provider_popup)
        def _(event):
            entry = self.model_setup_model.get_selected_popup_entry()
            if entry:
                if entry["kind"] == "action":
                    self._select_popup_action(entry)
                elif entry["kind"] == "provider":
                    p_name = entry["name"]
                    provs = self.model_setup_model.providers
                    if p_name in provs:
                        self.model_setup_model.provider_index = provs.index(p_name)
                    self.close_overlay()
                    asyncio.create_task(self._prepare_model_setup())

        # Add Custom Provider Overlay keybindings
        @kb.add("p", filter=add_provider)
        @kb.add("P", filter=add_provider)
        @kb.add("c-left", filter=add_provider)
        @kb.add("c-right", filter=add_provider)
        def _(event):
            delta = -1 if "left" in str(event.key_sequence[0].key) else 1
            pat = self.model_setup_model.cycle_pattern(delta)
            curr = self.add_provider_url_buffer.text.strip()
            known_defaults = [p.get("default_url", "") for p in self.model_setup_model.selected_pattern.__class__.__dict__.values() if isinstance(p, dict)]
            if not curr or curr in ("http://", "http://localhost:11434", "http://localhost:8000/v1", "https://api.anthropic.com", "https://generativelanguage.googleapis.com"):
                self.add_provider_url_buffer.text = pat.get("default_url", "http://")
            event.app.invalidate()

        @kb.add("tab", filter=add_provider)
        def _(event):
            if event.app.layout.current_control is self.add_provider_name_control:
                event.app.layout.focus(self.add_provider_url_control)
            else:
                event.app.layout.focus(self.add_provider_name_control)
            event.app.invalidate()

        @kb.add("enter", filter=add_provider)
        def _(event):
            if event.app.layout.current_control is self.add_provider_name_control:
                event.app.layout.focus(self.add_provider_url_control)
                event.app.invalidate()
            else:
                self.add_provider_url_buffer.validate_and_handle()

        @kb.add("enter", filter=provider_endpoint)
        def _(event): event.current_buffer.validate_and_handle()

        @kb.add("escape")
        def _(event): self.close_overlay()

        @kb.add("pageup")
        def _(event):
            self.state.follow_tail = False
            if hasattr(self, "transcript_window"):
                self.transcript_window.vertical_scroll = max(0, self.transcript_window.vertical_scroll - 10)
            if self.application: self.application.invalidate()

        @kb.add("pagedown")
        def _(event):
            if hasattr(self, "transcript_window"):
                self.transcript_window.vertical_scroll += 10
            self.state.follow_tail = True
            self.state.unseen_output = False
            if self.application: self.application.invalidate()

        @kb.add("c-up")
        @kb.add("s-up")
        def _(event):
            self.state.follow_tail = False
            if hasattr(self, "transcript_window"):
                self.transcript_window.vertical_scroll = max(0, self.transcript_window.vertical_scroll - 3)
            if self.application: self.application.invalidate()

        @kb.add("c-down")
        @kb.add("s-down")
        def _(event):
            if hasattr(self, "transcript_window"):
                self.transcript_window.vertical_scroll += 3
            self.state.follow_tail = True
            self.state.unseen_output = False
            if self.application: self.application.invalidate()

        @kb.add("c-home")
        def _(event):
            self.state.follow_tail = False
            if hasattr(self, "transcript_window"):
                self.transcript_window.vertical_scroll = 0
            if self.application: self.application.invalidate()

        @kb.add("end", filter=editor_focused & Condition(lambda: not self.prompt_buffer.text))
        @kb.add("c-end")
        def _(event):
            self.state.follow_tail = True
            self.state.unseen_output = False
            if hasattr(self, "transcript_window"):
                self.transcript_window.vertical_scroll = 10**9
            if self.application: self.application.invalidate()

        @kb.add("up", filter=editor_focused & Condition(lambda: not self.prompt_buffer.text and self.state.active_overlay is None))
        def _(event):
            self.state.follow_tail = False
            if hasattr(self, "transcript_window"):
                self.transcript_window.vertical_scroll = max(0, self.transcript_window.vertical_scroll - 3)
            if self.application: self.application.invalidate()

        @kb.add("down", filter=editor_focused & Condition(lambda: not self.prompt_buffer.text and self.state.active_overlay is None))
        def _(event):
            if hasattr(self, "transcript_window"):
                self.transcript_window.vertical_scroll += 3
            if self.application: self.application.invalidate()

        @kb.add("f10")
        def _(event):
            self.toggle_mouse_support()

        @kb.add("f12")
        def _(event):
            asyncio.create_task(self._open_model_setup_overlay())

        @kb.add("f4")
        @kb.add("c-t")
        def _(event):
            self.toggle_turn_mode()

        @kb.add("c-c")
        def _(event):
            if self.state.is_thinking or (self.bridge and self.bridge.is_active):
                if self.state.active_overlay:
                    self.close_overlay()
                asyncio.create_task(self.bridge.cancel())
                self.state.is_thinking = False
                self.prompt_buffer.reset()
                if self.application:
                    try:
                        self.application.layout.focus(self.prompt_control)
                    except Exception:
                        pass
                    self.application.invalidate()
            elif self.state.active_overlay:
                self.close_overlay()
            elif self.prompt_buffer.text:
                self.prompt_buffer.reset()
                if self.application:
                    self.application.invalidate()
            else:
                self.request_exit()

        @kb.add("c-d")
        def _(event):
            if not self.prompt_buffer.text and not self.state.is_thinking and not (self.bridge and self.bridge.is_active): self.request_exit()

        @kb.add("escape", "c-d")
        def _(event):
            self.request_exit()

        @kb.add("y", filter=permission)
        def _(event): asyncio.create_task(self.resolve_approval("once"))

        @kb.add("a", filter=permission)
        @kb.add("A", filter=permission)
        def _(event): asyncio.create_task(self.resolve_approval("always_workspace"))

        @kb.add("s", filter=permission)
        def _(event): asyncio.create_task(self.resolve_approval("always_session"))

        @kb.add("n", filter=permission)
        def _(event): asyncio.create_task(self.resolve_approval("deny"))

        @kb.add("N", filter=permission)
        def _(event): asyncio.create_task(self.resolve_approval("deny_all"))

        autonomy_cond = Condition(lambda: self.state.active_overlay == "autonomy_control")

        @kb.add("1", filter=autonomy_cond)
        def _(event):
            self.runtime.autonomy_store.set_preset("supervised")
            self.runtime.processor.registry.policy.autonomy = self.runtime.autonomy_store.get()
            self.state.add_toast("Perfil ativado: Supervisionado Estrito")
            if self.application: self.application.invalidate()

        @kb.add("2", filter=autonomy_cond)
        def _(event):
            self.runtime.autonomy_store.set_preset("balanced")
            self.runtime.processor.registry.policy.autonomy = self.runtime.autonomy_store.get()
            self.state.add_toast("Perfil ativado: Edição Livre de Arquivos (Always Allow)")
            if self.application: self.application.invalidate()

        @kb.add("3", filter=autonomy_cond)
        def _(event):
            self.runtime.autonomy_store.set_preset("autonomous")
            self.runtime.processor.registry.policy.autonomy = self.runtime.autonomy_store.get()
            self.state.add_toast("Perfil ativado: Autonomia Total")
            if self.application: self.application.invalidate()

        @kb.add("r", filter=autonomy_cond)
        def _(event):
            if hasattr(self.runtime.approval, "remembered_rules"):
                self.runtime.approval.remembered_rules.clear()
            self.state.add_toast("Regras salvas limpas.")
            if self.application: self.application.invalidate()

        @kb.add("escape", filter=autonomy_cond)
        def _(event):
            self.close_overlay()

        # Notice popup dismissal keybindings
        has_toasts = Condition(lambda: bool(self.state.active_toasts()))
        @kb.add("escape", filter=has_toasts & Condition(lambda: self.state.active_overlay is None))
        @kb.add("enter", filter=has_toasts & Condition(lambda: not self.prompt_buffer.text.strip() and self.state.active_overlay is None))
        def _(event):
            self.state.clear_toasts()
            if self.application: self.application.invalidate()

        @kb.add("enter", filter=palette)
        def _(event): asyncio.create_task(self._run_selected_palette())

        @kb.add("tab", filter=Condition(lambda: self.state.active_overlay is not None and self.state.active_overlay != "model_setup"))
        def _(event): event.app.layout.focus_next()

        @kb.add("s-tab", filter=Condition(lambda: self.state.active_overlay is not None and self.state.active_overlay != "model_setup"))
        def _(event): event.app.layout.focus_previous()

        return kb

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
        self._blocking_executor.shutdown(wait=False, cancel_futures=True)

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
            ("class:text.muted", "— Knowledge & Inference Task Tool • v1.0.0\n"),
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
                    elapsed = int(now - block.started_at)
                    if block.kind == "thought":
                        text = f"▸ Pensando ({elapsed}s...)"
                    else:
                        text = f"{text} ({elapsed}s...)"

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
        return PermissionCardComponent().render(self.state, max(50, self.state.width - 8))

    def _autonomy_text(self) -> str:
        t = DEFAULT_THEME
        curr = self.runtime.autonomy_store.get()
        rules = getattr(self.runtime.approval, "remembered_rules", [])
        rules_str = "\n".join(f"  • {r.tool_name} ({r.path_glob or '*'}) -> {r.decision.upper()} [{r.scope}]" for r in rules[-5:]) if rules else "  (Nenhuma regra salva)"

        return (
            t.format_primary("┌── CENTRAL DE PERMISSÕES & AUTONOMIA / AUTONOMY CONTROL ───────────────────┐\n") +
            f"│ Perfil Atual: [ {curr.level.upper()} ]  (Edição Livre de Arquivos: {curr.allow_file_write_auto})\n" +
            "│\n" +
            "│ Selecione um perfil de autonomia:\n" +
            "│  [1] Supervisionado Estrito : Pedir aprovação para cada arquivo / comando\n" +
            "│  [2] Edição Livre de Arquivos: SEMPRE PERMITIR alterações de arquivo (Always Allow)\n" +
            "│  [3] Autonomia Total        : Sempre permitir arquivos, comandos e subagentes\n" +
            "│\n" +
            "│ Regras Salvas no Workspace:\n" +
            f"{rules_str}\n" +
            "│\n" +
            "│ Controles: [1] Estrito  [2] Edição Livre  [3] Autonomia Total  [c] Limpar Regras  [Esc] Sair\n" +
            t.format_primary("└────────────────────────────────────────────────────────────────────────────┘")
        )

    def _palette_text(self):
        from kitt.ui.components.command_palette import CommandPaletteComponent
        return CommandPaletteComponent(self.commands).render(
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
            " [Tab] Alternar Cargo  |  [P / Espaço] Menu Provedores (★)  |  [L] Login/Auth  |  [Enter] Selecionar  |  [Esc] Fechar",
            " Atribuições de Modelos por Cargo:"
        ]
        for role in setup.roles:
            marker = ">" if role == setup.selected_role else " "
            profile = self._profile_for_role(role)
            endpoint = profile.base_url if profile else "?"
            lines.append(f" {marker} {role.title():10} {(profile.backend if profile else '?')}/{self._model_for_role(role)} @ {endpoint}")
        
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
        return "\n".join(f"{c.aliases[0]:16} {c.description}" for c in self.commands.commands.values())
