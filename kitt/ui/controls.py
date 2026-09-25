from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

def build_controls(ui) -> None:
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl

    app = ui

    class KittCompleter(Completer):
        def get_completions(self, document, complete_event):
            from kitt.skills.discovery import SkillDiscovery
            word = document.get_word_before_cursor(WORD=True)
            if not word:
                return

            roots = [
                Path(app.state.workspace_path) / ".kitt" / "skills",
                Path(app.state.workspace_path) / ".gemini" / "skills",
                Path.home() / ".kitt" / "skills",
                Path.home() / ".gemini" / "config" / "plugins",
                Path.home() / ".gemini" / "antigravity-cli" / "builtin" / "skills",
                Path.home() / ".claude" / "plugins",
            ]

            if word.startswith("/"):
                # 1. Built-in slash commands
                seen = set()
                for command in app.commands.search(word):
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
                    for path in Path(app.state.workspace_path).glob(prefix + "*"):
                        name = str(path.relative_to(app.state.workspace_path)) + ("/" if path.is_dir() else "")
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

    history_file = Path(ui.runtime.canonical_root) / ".kitt" / "prompt_history"
    try:
        history_file.parent.mkdir(parents=True, exist_ok=True)
        from prompt_toolkit.history import FileHistory
        ui.prompt_history = FileHistory(str(history_file))
    except Exception:
        from prompt_toolkit.history import InMemoryHistory
        ui.prompt_history = InMemoryHistory()

    ui.prompt_buffer = Buffer(
        multiline=True,
        history=ui.prompt_history,
        completer=KittCompleter(),
        complete_while_typing=True,
        accept_handler=ui._accept_prompt,
    )
    ui.prompt_control = BufferControl(buffer=ui.prompt_buffer, focusable=True)
    ui.palette_buffer = Buffer(multiline=False)
    ui.palette_buffer.on_text_changed += lambda _: ui._palette_changed()
    ui.palette_search_control = BufferControl(buffer=ui.palette_buffer, focusable=True)
    ui.provider_endpoint_buffer = Buffer(multiline=False, accept_handler=ui._accept_provider_endpoint)
    ui.provider_endpoint_control = BufferControl(buffer=ui.provider_endpoint_buffer, focusable=True)
    ui.home_control = FormattedTextControl(ui._home_text)
    ui.hints_control = FormattedTextControl(lambda: f"F4: Modo [{ui.state.turn_mode.upper()}]   F12: Modelos   Ctrl+P: Comandos   Alt+Enter: Nova Linha")
    ui.header_control = FormattedTextControl(ui._header_text)
    ui.transcript_control = FormattedTextControl(ui._transcript_text, get_cursor_position=ui._transcript_cursor_position, focusable=True)
    ui.sidebar_control = FormattedTextControl(ui._sidebar_text)
    ui.status_control = FormattedTextControl(ui._status_text)
    ui.permission_control = FormattedTextControl(ui._permission_text, focusable=True)
    from prompt_toolkit.layout import Window
    from prompt_toolkit.layout.margins import ScrollbarMargin
    ui.permission_window = Window(
        ui.permission_control,
        wrap_lines=True,
        right_margins=[ScrollbarMargin(display_arrows=True)],
    )
    ui.approval_menu_index = 0
    ui.palette_control = FormattedTextControl(ui._palette_text, focusable=True)
    ui.session_picker_control = FormattedTextControl(ui._session_picker_text, focusable=True)
    ui.timeline_control = FormattedTextControl(ui._timeline_text, focusable=True)
    ui.diff_control = FormattedTextControl(ui._diff_text, focusable=True)
    ui.pending_model_selection: Optional[Tuple[str, str, str, Optional[str]]] = None
    ui.model_setup_search_buffer = Buffer(multiline=False, accept_handler=ui._accept_model_setup_search)
    ui.model_setup_search_buffer.on_text_changed += lambda _: ui._model_setup_search_changed()
    ui.model_setup_search_control = BufferControl(buffer=ui.model_setup_search_buffer, focusable=True)
    ui.model_setup_header_control = FormattedTextControl(ui._model_setup_header_text)
    ui.model_setup_control = FormattedTextControl(ui._model_setup_text, focusable=True)
    ui.model_setup_control.mouse_handler = ui._model_setup_mouse_handler
    ui.provider_popup_control = FormattedTextControl(ui._provider_popup_text, focusable=True)
    ui.provider_popup_control.mouse_handler = ui._provider_popup_mouse_handler
    ui.add_provider_name_buffer = Buffer(multiline=False)
    ui.add_provider_name_control = BufferControl(buffer=ui.add_provider_name_buffer, focusable=True)
    ui.add_provider_url_buffer = Buffer(multiline=False, accept_handler=ui._accept_add_provider)
    ui.add_provider_url_control = BufferControl(buffer=ui.add_provider_url_buffer, focusable=True)
    ui.add_provider_help_control = FormattedTextControl(ui._add_provider_help_text)
    ui.autonomy_control = FormattedTextControl(ui._autonomy_text, focusable=True)
    ui.agents_control = FormattedTextControl(ui._agents_text, focusable=True)
    ui.live_agents_control = FormattedTextControl(ui._live_agents_text)
    ui.target_auth_provider: str | None = None
    ui.provider_endpoint_help_control = FormattedTextControl(ui._provider_endpoint_text)
    ui.auth_login_buffer = Buffer(multiline=False, accept_handler=ui._accept_auth_login)
    ui.auth_login_control = BufferControl(buffer=ui.auth_login_buffer, focusable=True)
    ui.auth_login_help_control = FormattedTextControl(ui._auth_login_help_text)
    ui.help_control = FormattedTextControl(ui._help_text, focusable=True)
    ui.toast_control = FormattedTextControl(ui._toast_text)
    ui._remote_server = None

