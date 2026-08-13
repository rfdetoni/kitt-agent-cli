from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import json
import os
import shlex
import urllib.request
import uuid
import time
from dataclasses import replace
from pathlib import Path

from kitt.core.turn_events import ApprovalRequired
from kitt.ui.commands import CommandRegistry
from kitt.ui.event_bridge import TurnEventBridge
from kitt.ui.layout import LayoutDimensions, build_root_container
from kitt.ui.overlay_models import DiffViewerModel, ModelSetupModel, OverlayFrame, SessionPickerModel, TimelineModel
from kitt.ui.reducer import reduce_ui_event
from kitt.ui.state import UIState, safe_text
from kitt.ui.theme import DEFAULT_THEME


class KittUIApp:
    """Single-owner full-screen prompt_toolkit application."""

    def __init__(self, runtime, mode: str = "auto", *, input=None, output=None, no_animation: bool = False):
        self.runtime = runtime
        self.mode = mode.lower()
        self.input = input
        self.output = output
        self.no_animation = no_animation
        root = Path(runtime.canonical_root)
        self.state = UIState(workspace_name=root.name or str(root), workspace_path=str(root))
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

        self.session_picker_model = SessionPickerModel(runtime)
        self.timeline_model = TimelineModel(runtime)
        self.diff_model = DiffViewerModel(str(root))
        self.model_setup_model = ModelSetupModel()

        self._build_controls()

    async def _run_blocking(self, func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        call = functools.partial(func, *args, **kwargs)
        return await loop.run_in_executor(self._blocking_executor, call)

    def _init_models_from_runtime(self) -> None:
        try:
            router = getattr(self.runtime.processor, "router", None)
            if router and hasattr(router, "config") and router.config:
                _, context = router.resolve_profile_for_task("context-gather")
                _, execute = router.resolve_profile_for_task("code-generation")
                self.state.small_model = context.model
                self.state.large_model = execute.model
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
                word = document.get_word_before_cursor(WORD=True)
                if word.startswith("/"):
                    for command in ui.commands.search(word):
                        alias = command.aliases[0]
                        yield Completion(alias, start_position=-len(word), display_meta=command.description)
                elif word.startswith("@"):
                    prefix = word[1:]
                    for path in Path(ui.state.workspace_path).glob(prefix + "*"):
                        name = str(path.relative_to(ui.state.workspace_path)) + ("/" if path.is_dir() else "")
                        yield Completion("@" + name, start_position=-len(word), display_meta="file")

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
        self.hints_control = FormattedTextControl(lambda: "Ctrl+P commands   Ctrl+X L sessions   Alt+Enter newline")
        self.header_control = FormattedTextControl(self._header_text)
        self.transcript_control = FormattedTextControl(self._transcript_text, get_cursor_position=self._transcript_cursor_position, focusable=True)
        self.sidebar_control = FormattedTextControl(self._sidebar_text)
        self.status_control = FormattedTextControl(self._status_text)
        self.permission_control = FormattedTextControl(self._permission_text, focusable=True)
        self.palette_control = FormattedTextControl(self._palette_text, focusable=True)
        self.session_picker_control = FormattedTextControl(self._session_picker_text, focusable=True)
        self.timeline_control = FormattedTextControl(self._timeline_text, focusable=True)
        self.diff_control = FormattedTextControl(self._diff_text, focusable=True)
        self.model_setup_control = FormattedTextControl(self._model_setup_text, focusable=True)
        self.autonomy_control = FormattedTextControl(self._autonomy_text, focusable=True)
        self.agents_control = FormattedTextControl(self._agents_text, focusable=True)
        self.live_agents_control = FormattedTextControl(self._live_agents_text)
        self.provider_endpoint_help_control = FormattedTextControl(self._provider_endpoint_text)
        self.help_control = FormattedTextControl(self._help_text, focusable=True)
        self.toast_control = FormattedTextControl(self._toast_text)

    def build_application(self):
        from prompt_toolkit.application import Application
        from prompt_toolkit.cursor_shapes import CursorShape
        from prompt_toolkit.layout import Layout
        root = build_root_container(self)
        self.application = Application(
            layout=Layout(root, focused_element=self.prompt_control),
            key_bindings=self._key_bindings(),
            style=DEFAULT_THEME.prompt_toolkit_style(),
            full_screen=True,
            cursor=CursorShape.BLINKING_BEAM,
            mouse_support=False,
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
        if endpoint.startswith(("http://", "https://")):
            buffer.reset()
            asyncio.get_running_loop().create_task(self._submit_provider_endpoint(endpoint))
        elif endpoint:
            self.state.add_toast("Endpoint must start with http:// or https://", persistent=True)
        return True

    async def submit(self, text: str) -> None:
        if text in {"/quit", "/exit"}:
            self.request_exit()
            return
        if text.startswith("/") and await self._execute_command(text):
            return
        if self.state.is_thinking or (self.bridge and self.bridge.is_active):
            return
        self.state.is_thinking = True
        conversation = self.runtime.history.get_or_create_active()
        try:
            await self.bridge.start(text, conversation["id"], explicit_files=self.explicit_files, no_history=not self.runtime.config.history_enabled)
        except Exception as err:
            self.state.is_thinking = False
            self.state.add_toast(f"Turn Error: {err}")

    def _on_event(self, event) -> None:
        reduce_ui_event(self.state, event)
        if isinstance(event, ApprovalRequired) and self.application:
            self.open_overlay("permission", self.permission_control)
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
            if not arg:
                await self._open_model_setup_overlay()
            else:
                role, model, provider, base_url = self._parse_model_command(arg)
                if model:
                    roles = self.model_setup_model.roles if role == "all" else (role,)
                    for selected_role in roles:
                        await self._set_model_role(selected_role, model, provider, base_url)
                    self._show_result(f"{role.title()} model saved and active: {(provider or self._profile_for_role(roles[0]).backend)}/{model}")
                else:
                    self._show_result("Usage: /model <principal|context|validation|all> [provider] <model> [base_url]")
        elif found.id == "setup_skills":
            skills = self.runtime.skills.list_skills()
            active = set(self.runtime.skills.get_active_skills())
            action, _, skill_name = arg.partition(" ")
            if action in {"enable", "disable"} and skill_name.strip():
                if action == "enable":
                    active.add(skill_name.strip())
                else:
                    active.discard(skill_name.strip())
                await self._run_blocking(self.runtime.skills.set_active_skills, sorted(active))
                self._show_result(f"Skill {action}d: {skill_name.strip()}")
            else:
                body = "\n".join(f"  {'[x]' if s.name in active else '[ ]'} {s.name} (v{s.version}) — {s.author}" for s in skills) if skills else "  No custom skills installed."
                self._show_result(f"Skill Configuration:\n\nInstalled Skills:\n{body}\n\n/setup-skills enable <name>\n/setup-skills disable <name>")
        elif found.id == "setup_models":
            if arg and not arg.startswith(("http://", "https://")):
                self._show_result("Usage: /setup-models [http://server:11434]")
            else:
                await self._open_model_setup_overlay(arg or None)
        elif found.id == "new":
            self._new_conversation()
        elif found.id == "history":
            await self._show_active_history()
        elif found.id == "thread":
            await self._show_history(arg)
        elif found.id in {"resume"}:
            target = arg or "1"
            conversation = await self._run_blocking(self.runtime.history.resume_conversation, target)
            if conversation:
                await self._load_conversation(conversation)
                self._show_result(f"Resumed: {conversation['title']}")
            else:
                self._show_result(f"Conversation not found: {target}")
        elif found.id == "conversation":
            conversation = self.runtime.history.get_or_create_active()
            self._show_result(f"Active conversation\n{conversation['id']}\n{conversation['title']}")
        elif found.id == "fork":
            conversation = await self._run_blocking(self.runtime.history.fork_conversation, title_suffix=f" ({arg})" if arg else " (Fork)")
            self._show_result(f"Forked: {conversation['title']}")
        elif found.id == "export_conversation":
            fmt = "json" if "json" in arg.lower() else "md"
            self._show_result(await self._run_blocking(self.runtime.history.export_conversation, fmt=fmt))
        elif found.id == "memory":
            self._show_result(self.runtime.memory.get_memory_context() or "No memory entries.")
        elif found.id == "remember":
            if arg:
                await self._run_blocking(self.runtime.memory.add_project_memory, arg)
                self._show_result(f"Remembered: {arg}")
            else:
                self._show_result("Usage: /remember <rule or guideline>")
        elif found.id == "clear_memory":
            await self._run_blocking(self.runtime.memory.clear_project_memory)
            self._show_result("Project memory cleared.")
        elif found.id == "skills":
            skills = self.runtime.skills.list_skills()
            self._show_result("\n".join(f"{s.name} v{s.version} — {s.author}" for s in skills) or "No skills installed.")
        elif found.id == "skill_install":
            if not arg:
                self._show_result("Usage: /skill-install <github/repo or URL>")
            else:
                try:
                    skill = await self._run_blocking(self.runtime.skills.install_from_git, arg)
                    self._show_result(f"Installed: {skill.name} v{skill.version}")
                except Exception as exc:
                    self._show_result(f"Install failed: {exc}")
        elif found.id == "skill_remove":
            removed = await self._run_blocking(self.runtime.skills.remove_skill, arg) if arg else False
            self._show_result("Usage: /skill-remove <skill_name>" if not arg else ("Removed." if removed else "Skill not found."))
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
            from kitt.cli.doctor import DoctorCheck
            results = await self._run_blocking(DoctorCheck(str(self.runtime.canonical_root)).run_diagnostics)
            self._show_result("\n".join(f"[{item['status']}] {item['name']}: {item['detail']}" for item in results))
        elif found.id == "diff":
            await self._open_diff_overlay()
        elif found.id == "status":
            snapshot = self.runtime.snapshot()
            self._show_result(f"Workspace: {snapshot.workspace_id}\nConversation: {snapshot.active_conversation_id}\nPending actions: {snapshot.pending_actions}\nQueued inputs: {snapshot.queued_inputs}")
        elif found.id == "stats":
            stats = await self._run_blocking(self.runtime.history.repo.get_telemetry_stats)
            self._show_result(f"Turns: {stats['count']}  Input: {stats['input']}  Output: {stats['output']}  Saved: {stats['saved']}")
        elif found.id == "context_stats":
            config = self.runtime.config
            self._show_result(f"Context window: {config.context_window_default}\nReserved output: {config.reserved_output_tokens}")
        elif found.id == "router":
            router = getattr(self.runtime.processor, "router", None)
            profiles = getattr(getattr(router, "config", None), "profiles", {})
            self._show_result("\n".join(f"{n}: {p.backend}/{p.model}" for n, p in profiles.items()) or "Router configuration unavailable.")
        elif found.id == "approvals":
            pending = self.runtime.approval.list_pending(self.runtime.workspace_id)
            self._show_result("\n".join(f"{r.approval_id[:8]} {r.tool_name} ({r.turn_id[:8]})\n  summary: {r.summary}" for r in pending) or "No approval requests.")
        elif found.id == "compact":
            conversation = self.runtime.history.get_or_create_active()
            result = await self._run_blocking(self.runtime.compaction.compact, conversation["id"], 4)
            self._show_result("History compacted." if result else "History already small.")
        elif found.id == "child":
            if not arg:
                self._show_result("Usage: /child <task description>")
            else:
                conversation = self.runtime.history.get_or_create_active()
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
            changeset = await self._run_blocking(self.runtime.processor.diff_applier.tracker.revert_last_changeset)
            self._show_result(f"Reverted changeset {changeset.id}." if changeset else "No changeset to revert.")
        elif found.id == "workspace":
            if not arg:
                self._show_result(str(self.runtime.canonical_root))
            else:
                await self._switch_workspace(arg)
        elif found.id == "run":
            if arg:
                await self._execute_direct_tool("run_command", {"command": arg})
            else:
                self._show_result("Usage: /run <command>")
        elif found.id == "commit":
            message = arg or "Auto-commit by K.I.T.T."
            await self._execute_direct_tool("run_command", {"command": f"git commit -am {shlex.quote(message)}"})
        else:
            self._show_result(f"/{found.aliases[0][1:]} executed.")
        if self.application:
            self.application.invalidate()
        return True

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
        return defaults.get(provider, ("http://localhost:11434", ""))

    async def _set_model_role(self, role: str, model: str, provider: str | None = None, base_url: str | None = None) -> None:
        router = self.runtime.processor.router
        profile_name, tasks = self._role_tasks(role)
        fallback = router.config.profiles.get(profile_name)
        if fallback is None:
            fallback = router.config.profiles.get("execute") or router.config.profiles.get("context")
        if fallback is None:
            raise RuntimeError("No provider profile available")
        provider = provider or fallback.backend
        default_url, default_key = self._provider_defaults(provider)
        router.config.profiles[profile_name] = replace(
            fallback, model=model, backend=provider,
            base_url=base_url or (fallback.base_url if fallback.backend == provider else default_url),
            api_key=fallback.api_key if fallback.backend == provider else default_key,
            max_output_tokens=max(fallback.max_output_tokens, 2048) if role == "principal" else fallback.max_output_tokens,
            supports_json=provider in {"openai", "anthropic", "gemini", "deepseek", "groq", "together", "mistral", "openrouter", "antigravity"},
        )
        for task in tasks:
            router.config.routing[task] = profile_name
        await self._run_blocking(router.save_config, self.state.workspace_path)
        self._init_models_from_runtime()
        self.state.add_toast(f"{role.title()} model: {provider}/{model}")

    async def _models_for_provider(self, provider: str, base_url: str) -> list[str]:
        from kitt.router.model_selector import ModelConfigurator
        defaults = {
            "openai": ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini", "gpt-4-turbo"],
            "anthropic": ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest"],
            "gemini": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash-exp", "gemini-1.5-pro"],
            "deepseek": ["deepseek-chat", "deepseek-reasoner", "deepseek-coder"],
            "groq": ["llama-3.3-70b-versatile", "mixtral-8x7b-32768", "deepseek-r1-distill-llama-70b"],
            "together": ["meta-llama/Llama-3.3-70B-Instruct-Turbo", "deepseek-ai/DeepSeek-R1", "Qwen/Qwen2.5-Coder-32B-Instruct"],
            "mistral": ["mistral-large-latest", "codestral-latest", "mistral-small-latest"],
            "openrouter": ["anthropic/claude-3.5-sonnet", "deepseek/deepseek-r1", "google/gemini-2.5-pro-exp-025", "openai/gpt-4o"],
            "xai": ["grok-2-latest", "grok-beta", "grok-2-vision-latest"],
            "fireworks": ["accounts/fireworks/models/deepseek-r1", "accounts/fireworks/models/llama-v3p3-70b-instruct"],
            "cohere": ["command-r-plus", "command-r", "command-light"],
            "azure": ["gpt-4o", "gpt-4o-mini"],
            "antigravity": ["ag-pro", "ag-flash", "gemini-2.5-pro", "gemini-2.5-flash"],
        }
        if provider == "ollama":
            return await self._run_blocking(ModelConfigurator(self.state.workspace_path).fetch_ollama_models, base_url)
        if provider == "lmstudio":
            def discover_lmstudio() -> list[str]:
                request = urllib.request.Request(f"{base_url.rstrip('/')}/v1/models", headers={"User-Agent": "Kitt-CLI"})
                try:
                    with urllib.request.urlopen(request, timeout=5) as response:
                        data = json.loads(response.read().decode("utf-8"))
                    return [item["id"] for item in data.get("data", []) if item.get("id")]
                except Exception:
                    return []
            return await self._run_blocking(discover_lmstudio)
        return defaults.get(provider, ["default-model"])

    async def _prepare_model_setup(self, base_url: str | None = None) -> None:
        self.state.status_text = "DISCOVERING MODELS"
        profile = self._profile_for_role(self.model_setup_model.selected_role)
        self.model_setup_model.base_url_override = base_url.rstrip("/") if base_url else None
        if base_url:
            self.model_setup_model.provider_index = self.model_setup_model.providers.index("ollama")
        elif profile and profile.backend in self.model_setup_model.providers:
            self.model_setup_model.provider_index = self.model_setup_model.providers.index(profile.backend)
        provider = self.model_setup_model.selected_provider
        base_url, _ = self._provider_defaults(provider)
        endpoint = self.model_setup_model.base_url_override or (profile.base_url if profile and profile.backend == provider else base_url)
        models = await self._models_for_provider(provider, endpoint)
        current = [self._model_for_role(role) for role in self.model_setup_model.roles]
        self.model_setup_model.models = list(dict.fromkeys([*models, *current]))
        if not self.model_setup_model.models:
            self.model_setup_model.models = current
        selected = self._model_for_role(self.model_setup_model.selected_role)
        self.model_setup_model.model_index = self.model_setup_model.models.index(selected) if selected in self.model_setup_model.models else 0
        self.state.status_text = "SYSTEM ONLINE"

    async def _open_model_setup_overlay(self, base_url: str | None = None) -> None:
        await self._prepare_model_setup(base_url)
        self.open_overlay("model_setup", self.model_setup_control)

    def _open_provider_endpoint_overlay(self) -> None:
        profile = self._profile_for_role(self.model_setup_model.selected_role)
        self.provider_endpoint_buffer.text = self.model_setup_model.base_url_override or (profile.base_url if profile and profile.backend == "ollama" else "http://")
        self.open_overlay("provider_endpoint", self.provider_endpoint_control)

    async def _submit_provider_endpoint(self, endpoint: str) -> None:
        endpoint = endpoint.strip().rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            self.state.add_toast("Endpoint must start with http:// or https://", persistent=True)
            return
        self.close_overlay()
        await self._prepare_model_setup(endpoint)
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
            return " " + " | ".join(items) + "  (press 'a' for dashboard)"
        else:
            done = [tk for tk in tasks if tk.status == "done"]
            err = [tk for tk in tasks if tk.status == "error"]
            if err:
                return f" ✖ [FALHA NO PROCESSO] {len(err)} tarefa(s) com erro | {len(done)} concluída(s)"
            return f" ✔ [PROCESSO CONCLUÍDO] {len(done)} tarefa(s)/agente(s) finalizados com sucesso!"

    async def _apply_selected_model(self) -> None:
        model = self.model_setup_model.selected_model
        if not model:
            self.state.add_toast("No model selected", persistent=True)
            return
        role = self.model_setup_model.selected_role
        try:
            provider = self.model_setup_model.selected_provider
            profile = self._profile_for_role(role)
            base_url = self.model_setup_model.base_url_override or (profile.base_url if profile and profile.backend == provider else self._provider_defaults(provider)[0])
            await self._set_model_role(role, model, provider, base_url)
            self._show_result(f"{role.title()} saved: {provider}/{model}")
            self.close_overlay()
        except Exception as exc:
            self.state.add_toast(f"Model update failed: {exc}", persistent=True)
        if self.application:
            self.application.invalidate()

    def _show_result(self, text: str) -> None:
        self.state.route = "session"
        self.state.append_message("system", safe_text(text)[:12000])

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
            new_runtime = await self._run_blocking(self.runtime.switch_workspace, str(target))
        except Exception as exc:
            self._show_result(f"Workspace switch failed: {exc}")
            return
        self.runtime = new_runtime
        self.state.workspace_path = str(new_runtime.canonical_root)
        self.state.workspace_name = new_runtime.canonical_root.name or str(new_runtime.canonical_root)
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

        if mode is True or mode == "once":
            allow = True
        elif mode is False or mode == "deny":
            allow = False
        elif mode in {"always_workspace", "always", "A"}:
            allow = True
            tool_name = pending.get("tool_name", "apply_patch")
            self.runtime.approval.remember(tool_name, "**", "allow", "workspace")
            self.runtime.autonomy_store.set_preset("balanced")
            self.runtime.processor.registry.policy.autonomy = self.runtime.autonomy_store.get()
            self.state.add_toast(f"Sempre permitir {tool_name} ativado para este workspace.")
        elif mode in {"always_session", "session", "s"}:
            allow = True
            tool_name = pending.get("tool_name", "apply_patch")
            self.runtime.approval.remember(tool_name, "**", "allow", "session")
            self.state.add_toast(f"Sempre permitir {tool_name} nesta sessão.")
        elif mode == "deny_all":
            allow = False
            for req in list(self.state.pending_approvals):
                try:
                    self.runtime.approval.deny(req["approval_id"], "Denied all in queue")
                except Exception:
                    pass
            self.state.pending_approvals.clear()
            self.close_overlay()
            await self.bridge.cancel("Denied all in queue")
            if self.application: self.application.invalidate()
            return
        else:
            allow = bool(mode)

        self.state.status_text = "APPROVING" if allow else "DENYING"
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

    def open_overlay(self, name: str, control=None) -> None:
        curr_focus = self.application.layout.current_control if self.application else None
        frame = OverlayFrame(name=name, previous_focus=curr_focus, preferred_focus=control)
        self.focus_stack.append(frame)
        self.state.push_overlay(name)
        if self.application:
            self.application.invalidate()
            if control:
                try:
                    self.application.layout.focus(control)
                except ValueError:
                    # Conditional float joins focus tree on next render.
                    asyncio.get_running_loop().call_soon(self._focus_if_visible, control)

    def _focus_if_visible(self, control) -> None:
        if not self.application:
            return
        try:
            self.application.layout.focus(control)
        except ValueError:
            pass

    def close_overlay(self) -> None:
        self.state.pop_overlay()
        frame = self.focus_stack.pop() if self.focus_stack else None
        if self.application:
            target = None
            if self.focus_stack and self.focus_stack[-1].preferred_focus:
                target = self.focus_stack[-1].preferred_focus
            elif frame and frame.previous_focus:
                target = frame.previous_focus
            else:
                target = getattr(self, "prompt_control", None)

            if target:
                try:
                    self.application.layout.focus(target)
                except ValueError:
                    try:
                        if hasattr(self, "prompt_control"):
                            self.application.layout.focus(self.prompt_control)
                    except ValueError:
                        pass
            self.application.invalidate()

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
        provider_endpoint = Condition(lambda: self.state.active_overlay == "provider_endpoint")
        editor_focused = Condition(lambda: self.application and self.application.layout.current_control is self.prompt_control)
        can_submit = Condition(lambda: bool(self.prompt_buffer.text.strip()) and not self.state.is_thinking and not (self.bridge and self.bridge.is_active))

        @kb.add("enter", filter=editor_focused & can_submit)
        def submit_prompt(event):
            event.current_buffer.validate_and_handle()

        @kb.add("escape", "enter", filter=editor_focused)
        def insert_newline(event):
            event.current_buffer.insert_text("\n")

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

        @kb.add("a", filter=~editor_focused & ~palette & ~permission)
        @kb.add("c-x", "a")
        def _(event): self.open_overlay("agents", self.agents_control)

        @kb.add("c-x", "s")
        def _(event): self.state.add_toast(self._status_text()); event.app.invalidate()

        @kb.add("c-o", filter=~editor_focused & ~palette)
        def _(event): self.state.toggle_last_tool_collapse(); event.app.invalidate()

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

        @kb.add("c-end")
        def _(event):
            self.state.follow_tail = True
            self.state.unseen_output = False
            if hasattr(self, "transcript_window"):
                self.transcript_window.vertical_scroll = 10**9
            if self.application: self.application.invalidate()

        @kb.add("c-c")
        def _(event):
            if self.state.active_overlay: self.close_overlay()
            elif self.state.is_thinking or (self.bridge and self.bridge.is_active): asyncio.create_task(self.bridge.cancel())
            else: self.request_exit()

        @kb.add("c-d")
        def _(event):
            if not self.prompt_buffer.text and not self.state.is_thinking and not (self.bridge and self.bridge.is_active): self.request_exit()

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
        if selected not in self.model_setup_model.models:
            self.model_setup_model.models.append(selected)
        self.model_setup_model.model_index = self.model_setup_model.models.index(selected)
        if self.application:
            self.application.invalidate()

    async def _move_model_provider(self, amount: int) -> None:
        self.model_setup_model.move_provider(amount)
        self.model_setup_model.base_url_override = None
        provider = self.model_setup_model.selected_provider
        base_url, _ = self._provider_defaults(provider)
        self.model_setup_model.models = await self._models_for_provider(provider, base_url)
        current = self._model_for_role(self.model_setup_model.selected_role)
        if current not in self.model_setup_model.models:
            self.model_setup_model.models.append(current)
        self.model_setup_model.model_index = self.model_setup_model.models.index(current) if current in self.model_setup_model.models else 0
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
        self._blocking_executor.shutdown(wait=False, cancel_futures=True)

    def _home_text(self):
        scanner = DEFAULT_THEME.scanner_frame(self.state.scanner_step, 22)
        return [
            ("class:primary.bright", f"\n       [{scanner}]\n"),
            ("class:primary", "          K.I.T.T.\n"),
            ("class:text.muted", " Knowledge & Inference Task Tool\n"),
            ("class:accent", f" {self.state.workspace_path}\n"),
            ("class:text.muted", f" {self.state.small_model} / {self.state.large_model}")
        ]

    def _header_text(self):
        return [("class:primary", " K.I.T.T. "), ("class:text.muted", self.state.workspace_path)]

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
                    out.append(("class:tool", f"{first_line} (ctrl+o para expandir)\n"))
                elif "full_output" in block.metadata:
                    out.append(("class:tool", f"{text}\n    {block.metadata['full_output']}\n    (ctrl+o para recolher)\n"))
                else:
                    out.append(("class:tool", f"{text}\n"))
            else:
                label = labels.get(block.kind, block.kind.upper())
                out += [(f"class:{block.kind}", f"\n{label}  "), ("class:text", block.text + "\n")]
        if self.state.unseen_output:
            out.append(("class:warning", "\n[new output below]"))
        return out or [("class:text.muted", " Start conversation below.")]

    def _transcript_cursor_position(self):
        from prompt_toolkit.data_structures import Point
        if not self.state.follow_tail:
            return None
        text_content = self._transcript_text()
        total_lines = 0
        for style, txt in text_content:
            total_lines += txt.count("\n")
        return Point(x=0, y=max(0, total_lines - 1))

    def _sidebar_text(self):
        pct = min(100, self.state.tokens_used * 100 // max(1, self.state.context_window))
        return (
            f" WORKSPACE\n {self.state.workspace_name}\n\n"
            f" CONVERSATION\n {(self.state.active_conversation_id or 'new')[:12]}\n\n"
            f" MODELS\n {self.state.small_model}\n {self.state.large_model}\n\n"
            f" CONTEXT\n {self.state.tokens_used}/{self.state.context_window} ({pct}%)\n"
            f" SAVED {self.state.net_saved_tokens}"
        )

    def _status_text(self):
        pct = min(100, self.state.tokens_used * 100 // max(1, self.state.context_window))
        if self.state.width < 80:
            return f" {self.state.status_text} | {self.state.large_model[:16]} | {pct}% "
        return f" {self.state.workspace_name} | {self.state.status_text} | {self.state.large_model} | context {pct}% "

    def _toast_text(self) -> str:
        toasts = self.state.active_toasts()
        if not toasts:
            return ""
        t = toasts[-1]
        return f" {t.text}\n\n  [ Enter / Esc ] OK (Fechar Aviso)"

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
        rows = self.commands.search(self.palette_buffer.text)
        if not rows:
            return "No commands"
        start = min(max(0, self.palette_index - 5), max(0, len(rows) - 12))
        visible = rows[start:start + 12]
        return "\n".join(
            ("> " if start + i == self.palette_index else "  ") + f"{c.aliases[0]}  {c.title} [{c.category}]\n    {c.description}"
            for i, c in enumerate(visible)
        )

    def _session_picker_text(self):
        sessions = self.session_picker_model.sessions
        if not sessions:
            return "Search sessions\n\nNo conversations found."
        lines = ["Search sessions (Enter to resume)\n"]
        for idx, s in enumerate(sessions):
            prefix = "> " if idx == self.session_picker_model.selected_index else "  "
            lines.append(f"{prefix}{s.get('id', '')[:8]}  {s.get('title', 'Untitled')}")
        return "\n".join(lines)

    def _timeline_text(self):
        turns = self.timeline_model.turns
        if not turns:
            return "Conversation timeline\n\nNo turns in active conversation."
        lines = ["Turn Timeline\n"]
        for idx, t in enumerate(turns):
            prefix = "> " if idx == self.timeline_model.selected_index else "  "
            lines.append(f"{prefix}Turn {t.get('ordinal', idx+1)} ({t.get('id', '')[:8]})")
        return "\n".join(lines)

    def _diff_text(self):
        diff = self.diff_model.diff_text
        if not diff:
            return "Unified diff preview\n\nNo pending diff."
        lines = diff.splitlines()[self.diff_model.scroll_offset:self.diff_model.scroll_offset + 30]
        return "Unified diff preview (Use Up/Down to scroll)\n\n" + "\n".join(lines)

    def _model_setup_text(self):
        setup = self.model_setup_model
        lines = ["Tab: role  Shift+Tab/Ctrl+Left/Right: provider  Up/Down: model  Ctrl+Alt++: add Ollama endpoint  Enter: save\n", "Remote Ollama: Ctrl+Alt++ (or Ctrl+X A) then URL. Custom URL: /model <role> <provider> <model> <base_url>\n", "Role assignments:"]
        for role in setup.roles:
            marker = ">" if role == setup.selected_role else " "
            profile = self._profile_for_role(role)
            endpoint = profile.base_url if profile else "?"
            lines.append(f"{marker} {role.title():10} {(profile.backend if profile else '?')}/{self._model_for_role(role)} @ {endpoint}")
        endpoint = setup.base_url_override or (self._profile_for_role(setup.selected_role).base_url if self._profile_for_role(setup.selected_role) else "")
        lines.append(f"\nProvider: {setup.selected_provider} @ {endpoint} (keys use environment API key when required)")
        lines.append("\nAvailable models:")
        for index, model in enumerate(setup.models[:30]):
            marker = ">" if index == setup.model_index else " "
            lines.append(f"{marker} {model}")
        return "\n".join(lines)

    @staticmethod
    def _provider_endpoint_text():
        return "Remote Ollama URL, including port. Example: http://192.168.1.50:11434\n\nEnter: discover models   Esc: cancel"

    def _help_text(self):
        return "\n".join(f"{c.aliases[0]:16} {c.description}" for c in self.commands.commands.values())
