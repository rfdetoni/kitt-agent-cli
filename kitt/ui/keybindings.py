from __future__ import annotations

import asyncio

def build_key_bindings(ui):
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.key_binding import KeyBindings
    from kitt.ui.components.permission_card import PermissionCardComponent
    kb = KeyBindings()

    permission = Condition(lambda: ui.state.active_overlay == "permission")
    palette = Condition(lambda: ui.state.active_overlay == "palette")
    session_picker = Condition(lambda: ui.state.active_overlay == "session_picker")
    timeline = Condition(lambda: ui.state.active_overlay == "timeline")
    diff_overlay = Condition(lambda: ui.state.active_overlay == "diff")
    model_setup = Condition(lambda: ui.state.active_overlay == "model_setup")
    provider_popup = Condition(lambda: ui.state.active_overlay == "provider_popup")
    add_provider = Condition(lambda: ui.state.active_overlay == "add_provider")
    provider_endpoint = Condition(lambda: ui.state.active_overlay == "provider_endpoint")
    auth_login = Condition(lambda: ui.state.active_overlay == "auth_login")\n    reverse_proxy = Condition(lambda: ui.state.active_overlay == "reverse_proxy")
    editor_focused = Condition(lambda: ui.application and ui.application.layout.current_control is ui.prompt_control)
    context_panel = Condition(lambda: ui.state.active_overlay in {"session_picker", "timeline", "diff", "agents", "autonomy_control", "reverse_proxy", "help"})

    @kb.add("tab", filter=auth_login)
    def _(event):
        prov = ui.target_auth_provider or "openai"
        from kitt.llm.auth import ProviderAuthService
        env_var = ProviderAuthService.get_default_env_var(prov)
        if ProviderAuthService.get_env_value(env_var):
            ui.auth_login_buffer.text = "env"
            event.app.invalidate()
    can_submit = Condition(
        lambda: bool(ui.prompt_buffer.text.strip()) and (
            ui.prompt_buffer.text.strip().startswith("/")
            or (not ui.state.is_thinking and not (ui.bridge and ui.bridge.is_active))
        )
    )

    @kb.add("enter", filter=editor_focused & can_submit)
    def submit_prompt(event):
        event.current_buffer.validate_and_handle()

    @kb.add("enter", filter=editor_focused & ~can_submit)
    def submit_blocked_feedback(event):
        if ui.state.is_thinking or (ui.bridge and ui.bridge.is_active):
            ui.state.add_toast("Execução em andamento. Pressione Ctrl+C para cancelar antes de enviar novo comando.", duration=3.0)
            if ui.application:
                ui.application.invalidate()

    @kb.add("escape", "enter", filter=editor_focused)
    def insert_newline(event):
        event.current_buffer.insert_text("\n")

    @kb.add("f1")
    @kb.add("?", filter=~editor_focused)
    def _(event):
        ui.open_overlay("help", ui.help_control)

    @kb.add("c-p", filter=~palette)
    def _(event): ui.open_overlay("palette", ui.palette_search_control)

    @kb.add("c-n", filter=palette)
    @kb.add("down", filter=palette)
    def _(event): ui._move_palette(1)

    @kb.add("c-p", filter=palette)
    @kb.add("up", filter=palette)
    def _(event): ui._move_palette(-1)

    @ui.keymap.bind(kb, "new_session")
    def _(event): ui._new_conversation()

    @ui.keymap.bind(kb, "toggle_sidebar")
    def _(event): ui._toggle_sidebar()

    @ui.keymap.bind(kb, "agents")
    def _(event): ui.open_overlay("agents", ui.agents_control)

    @ui.keymap.bind(kb, "collapse_tool", filter=~palette & ~auth_login & ~model_setup)
    def _(event): ui.state.toggle_last_tool_collapse(); event.app.invalidate()

    @ui.keymap.bind(kb, "reasoning_up", filter=~model_setup & ~palette)
    def increase_reasoning(event):
        target = min(100, ui.state.reasoning_effort + 10)
        asyncio.create_task(ui._set_reasoning_effort(target))

    @ui.keymap.bind(kb, "reasoning_down", filter=~model_setup & ~palette)
    def decrease_reasoning(event):
        target = max(0, ui.state.reasoning_effort - 10)
        asyncio.create_task(ui._set_reasoning_effort(target))

    @kb.add("left", filter=context_panel)
    def _(event):
        ui.overlay_manager.cycle_context_tab(-1)

    @kb.add("right", filter=context_panel)
    def _(event):
        ui.overlay_manager.cycle_context_tab(1)


    @kb.add("down", filter=reverse_proxy)
    def _(event):
        ui._reverse_proxy_move(1)

    @kb.add("up", filter=reverse_proxy)
    def _(event):
        ui._reverse_proxy_move(-1)

    @kb.add("f5", filter=reverse_proxy)
    def _(event):
        asyncio.create_task(ui._refresh_reverse_proxy())

    @kb.add("n", filter=reverse_proxy)
    def _(event):
        ui._reverse_proxy_show("plugins")

    @kb.add("p", filter=reverse_proxy)
    def _(event):
        ui._reverse_proxy_show("profiles")

    @kb.add("i", filter=reverse_proxy)
    def _(event):
        ui._reverse_proxy_show("instances")

    @kb.add("tab", filter=reverse_proxy)
    def _(event):
        ui._reverse_proxy_cycle_profile(1)

    @kb.add("u", filter=reverse_proxy)
    def _(event):
        ui._reverse_proxy_prepare_url()

    @kb.add("a", filter=reverse_proxy)
    def _(event):
        if ui.reverse_proxy_model.page == "profiles":
            ui._reverse_proxy_prepare_profile_create()

    @kb.add("d", filter=reverse_proxy)
    def _(event):
        if ui.reverse_proxy_model.page == "profiles":
            asyncio.create_task(ui._reverse_proxy_remove_profile())

    @kb.add("enter", filter=reverse_proxy)
    def _(event):
        if ui.reverse_proxy_model.page == "plugins":
            asyncio.create_task(ui._reverse_proxy_start_selected())

    @kb.add("r", filter=reverse_proxy)
    def _(event):
        if ui.reverse_proxy_model.page == "instances":
            asyncio.create_task(ui._reverse_proxy_restart_selected())

    @kb.add("x", filter=reverse_proxy)
    def _(event):
        if ui.reverse_proxy_model.page == "instances":
            asyncio.create_task(ui._reverse_proxy_stop_selected())

    @kb.add("c", filter=reverse_proxy)
    def _(event):
        asyncio.create_task(ui._reverse_proxy_bind_selected("context"))

    @kb.add("e", filter=reverse_proxy)
    def _(event):
        asyncio.create_task(ui._reverse_proxy_bind_selected("principal"))

    @kb.add("v", filter=reverse_proxy)
    def _(event):
        asyncio.create_task(ui._reverse_proxy_bind_selected("validation"))

    @kb.add("down", filter=session_picker)
    @kb.add("c-n", filter=session_picker)
    def _(event):
        ui.session_picker_model.move_selection(1)
        event.app.invalidate()

    @kb.add("up", filter=session_picker)
    @kb.add("c-p", filter=session_picker)
    def _(event):
        ui.session_picker_model.move_selection(-1)
        event.app.invalidate()

    @kb.add("enter", filter=session_picker)
    def _(event):
        sel = ui.session_picker_model.get_selected()
        if sel:
            ui.close_overlay()
            asyncio.create_task(ui._execute_command(f"/resume {sel['id']}"))

    @kb.add("down", filter=timeline)
    @kb.add("c-n", filter=timeline)
    def _(event):
        ui.timeline_model.move_selection(1)
        event.app.invalidate()

    @kb.add("up", filter=timeline)
    @kb.add("c-p", filter=timeline)
    def _(event):
        ui.timeline_model.move_selection(-1)
        event.app.invalidate()

    @kb.add("down", filter=diff_overlay)
    def _(event):
        ui.diff_model.scroll(1)
        event.app.invalidate()

    @kb.add("up", filter=diff_overlay)
    def _(event):
        ui.diff_model.scroll(-1)
        event.app.invalidate()

    @kb.add("down", filter=model_setup)
    @kb.add("c-n", filter=model_setup)
    def _(event):
        ui.model_setup_model.move_model(1)
        event.app.invalidate()

    @kb.add("up", filter=model_setup)
    @kb.add("c-p", filter=model_setup)
    def _(event):
        ui.model_setup_model.move_model(-1)
        event.app.invalidate()

    @kb.add("p", filter=model_setup)
    @kb.add("P", filter=model_setup)
    @kb.add("space", filter=model_setup)
    def _(event):
        ui._open_provider_popup_overlay()

    @kb.add("l", filter=model_setup)
    @kb.add("L", filter=model_setup)
    def _(event):
        prov = ui.model_setup_model.selected_provider
        ui._open_auth_login_overlay(prov, parent_name="model_setup")

    @kb.add("t", filter=model_setup)
    @kb.add("T", filter=model_setup)
    def _(event):
        asyncio.create_task(ui._toggle_role_local_limits(ui.model_setup_model.selected_role))

    @kb.add("a", filter=model_setup)
    @kb.add("A", filter=model_setup)
    @kb.add("+", filter=model_setup)
    def _(event):
        ui._open_add_provider_overlay()

    @kb.add("tab", filter=model_setup)
    def _(event):
        asyncio.create_task(ui._move_model_role(1))

    @kb.add("s-tab", filter=model_setup)
    def _(event):
        asyncio.create_task(ui._move_model_provider(-1))

    @kb.add("c-right", filter=model_setup)
    def _(event): asyncio.create_task(ui._move_model_provider(1))

    @kb.add("c-left", filter=model_setup)
    def _(event): asyncio.create_task(ui._move_model_provider(-1))

    @kb.add("escape", "c-@", filter=model_setup)
    @kb.add("c-x", "a", filter=model_setup)
    def _(event): ui._open_provider_endpoint_overlay()

    @kb.add("enter", filter=model_setup)
    def _(event): asyncio.create_task(ui._apply_selected_model())

    # Provider Popup Dropdown keybindings
    @kb.add("down", filter=provider_popup)
    @kb.add("c-n", filter=provider_popup)
    def _(event):
        ui.model_setup_model.move_popup_selection(1)
        event.app.invalidate()

    @kb.add("up", filter=provider_popup)
    @kb.add("c-p", filter=provider_popup)
    def _(event):
        ui.model_setup_model.move_popup_selection(-1)
        event.app.invalidate()

    @kb.add("f", filter=provider_popup)
    @kb.add("F", filter=provider_popup)
    @kb.add("space", filter=provider_popup)
    def _(event):
        entry = ui.model_setup_model.get_selected_popup_entry()
        if entry and entry["kind"] == "provider":
            is_fav = ui.model_setup_model.toggle_favorite(entry["name"])
            tag = "adicionado aos favoritos ★" if is_fav else "removido dos favoritos ☆"
            ui.state.add_toast(f"Provedor '{entry['name']}': {tag}", persistent=False)
            event.app.invalidate()

    @kb.add("a", filter=provider_popup)
    @kb.add("A", filter=provider_popup)
    @kb.add("+", filter=provider_popup)
    def _(event):
        ui._open_add_provider_overlay()

    @kb.add("e", filter=provider_popup)
    @kb.add("E", filter=provider_popup)
    def _(event):
        entry = ui.model_setup_model.get_selected_popup_entry()
        if entry:
            if entry["kind"] == "provider" and ui.model_setup_model.get_custom_provider(entry["name"]):
                ui.close_overlay()
                ui._open_edit_provider_overlay(entry["name"])
            elif entry["kind"] == "action" and entry.get("name", "").startswith("edit_provider_"):
                ui._select_popup_action(entry)

    @kb.add("d", filter=provider_popup)
    @kb.add("D", filter=provider_popup)
    @kb.add("delete", filter=provider_popup)
    def _(event):
        entry = ui.model_setup_model.get_selected_popup_entry()
        if entry:
            if entry["kind"] == "provider" and ui.model_setup_model.get_custom_provider(entry["name"]):
                ui.close_overlay()
                asyncio.create_task(ui._delete_custom_provider(entry["name"]))
            elif entry["kind"] == "action" and entry.get("name", "").startswith("delete_provider_"):
                ui._select_popup_action(entry)

    @kb.add("enter", filter=provider_popup)
    def _(event):
        entry = ui.model_setup_model.get_selected_popup_entry()
        if entry:
            if entry["kind"] == "action":
                ui._select_popup_action(entry)
            elif entry["kind"] == "provider":
                p_name = entry["name"]
                provs = ui.model_setup_model.providers
                if p_name in provs:
                    ui.model_setup_model.provider_index = provs.index(p_name)
                ui.close_overlay()
                asyncio.create_task(ui._prepare_model_setup())

    # Add Custom Provider Overlay keybindings
    @kb.add("p", filter=add_provider)
    @kb.add("P", filter=add_provider)
    @kb.add("c-left", filter=add_provider)
    @kb.add("c-right", filter=add_provider)
    def _(event):
        delta = -1 if "left" in str(event.key_sequence[0].key) else 1
        pat = ui.model_setup_model.cycle_pattern(delta)
        curr = ui.add_provider_url_buffer.text.strip()
        known_defaults = [p.get("default_url", "") for p in ui.model_setup_model.selected_pattern.__class__.__dict__.values() if isinstance(p, dict)]
        if not curr or curr in ("http://", "http://localhost:11434", "http://localhost:8000/v1", "https://api.anthropic.com", "https://generativelanguage.googleapis.com"):
            ui.add_provider_url_buffer.text = pat.get("default_url", "http://")
        event.app.invalidate()

    @kb.add("tab", filter=add_provider)
    def _(event):
        if event.app.layout.current_control is ui.add_provider_name_control:
            event.app.layout.focus(ui.add_provider_url_control)
        else:
            event.app.layout.focus(ui.add_provider_name_control)
        event.app.invalidate()

    @kb.add("enter", filter=add_provider)
    def _(event):
        if event.app.layout.current_control is ui.add_provider_name_control:
            event.app.layout.focus(ui.add_provider_url_control)
            event.app.invalidate()
        else:
            ui.add_provider_url_buffer.validate_and_handle()

    @kb.add("enter", filter=provider_endpoint)
    def _(event): event.current_buffer.validate_and_handle()

    @kb.add("escape")
    def _(event): ui.close_overlay()

    @kb.add("pageup")
    def _(event):
        ui._scroll_transcript(-10)

    @kb.add("pagedown")
    def _(event):
        ui._scroll_transcript(10)

    @kb.add("c-up")
    @kb.add("s-up")
    def _(event):
        ui._scroll_transcript(-3)

    @kb.add("c-down")
    @kb.add("s-down")
    def _(event):
        ui._scroll_transcript(3)

    @kb.add("c-home")
    def _(event):
        ui.state.follow_tail = False
        if hasattr(ui, "transcript_window"):
            ui.transcript_window.vertical_scroll = 0
        if ui.application: ui.application.invalidate()

    @kb.add("end", filter=editor_focused & Condition(lambda: not ui.prompt_buffer.text))
    @kb.add("c-end")
    def _(event):
        ui.state.follow_tail = True
        ui.state.unseen_output = False
        if hasattr(ui, "transcript_window"):
            ui.transcript_window.vertical_scroll = 10**9
        if ui.application: ui.application.invalidate()

    @kb.add("up", filter=editor_focused & Condition(lambda: ui.state.active_overlay is None))
    def _(event):
        buf = event.current_buffer
        if buf.complete_state:
            buf.complete_previous()
            if ui.application: ui.application.invalidate()
            return

        if buf.document.cursor_position_row == 0:
            prev_text = buf.text
            buf.history_backward()
            if buf.text != prev_text:
                if ui.application: ui.application.invalidate()
                return
        elif buf.document.cursor_position_row > 0:
            buf.cursor_up()
            if ui.application: ui.application.invalidate()
            return

        ui._scroll_transcript(-3)

    @kb.add("down", filter=editor_focused & Condition(lambda: ui.state.active_overlay is None))
    def _(event):
        buf = event.current_buffer
        if buf.complete_state:
            buf.complete_next()
            if ui.application: ui.application.invalidate()
            return

        if buf.document.cursor_position_row == buf.document.line_count - 1:
            prev_text = buf.text
            buf.history_forward()
            if buf.text != prev_text:
                if ui.application: ui.application.invalidate()
                return
        elif buf.document.cursor_position_row < buf.document.line_count - 1:
            buf.cursor_down()
            if ui.application: ui.application.invalidate()
            return

        ui._scroll_transcript(3)

    @ui.keymap.bind(kb, "mouse")
    def _(event):
        ui.toggle_mouse_support()

    @ui.keymap.bind(kb, "models")
    def _(event):
        asyncio.create_task(ui._open_model_setup_overlay())

    @ui.keymap.bind(kb, "mode")
    def _(event):
        ui.toggle_turn_mode()

    @ui.keymap.bind(kb, "cancel")
    def _(event):
        if ui.state.is_thinking or (ui.bridge and ui.bridge.is_active):
            if ui.state.active_overlay:
                ui.close_overlay()
            ui.state.is_thinking = False
            ui.state.status_text = "SYSTEM ONLINE"
            if ui.bridge:
                asyncio.create_task(ui.bridge.cancel())
            ui.prompt_buffer.reset()
            if ui.application:
                try:
                    ui.application.layout.focus(ui.prompt_control)
                except Exception:
                    pass
                ui.application.invalidate()
        elif ui.state.active_overlay:
            ui.close_overlay()
        elif ui.prompt_buffer.text:
            ui.prompt_buffer.reset()
            if ui.application:
                ui.application.invalidate()
        else:
            ui.request_exit()

    @kb.add("c-d")
    def _(event):
        if not ui.prompt_buffer.text and not ui.state.is_thinking and not (ui.bridge and ui.bridge.is_active): ui.request_exit()

    @kb.add("escape", "c-d")
    def _(event):
        ui.request_exit()

    @kb.add("y", filter=permission)
    def _(event): asyncio.create_task(ui.resolve_approval("once"))

    @kb.add("up", filter=permission)
    @kb.add("s-tab", filter=permission)
    def _(event):
        ui.approval_menu_index = (ui.approval_menu_index - 1) % len(PermissionCardComponent.ACTIONS)
        event.app.invalidate()

    @kb.add("down", filter=permission)
    @kb.add("tab", filter=permission)
    def _(event):
        ui.approval_menu_index = (ui.approval_menu_index + 1) % len(PermissionCardComponent.ACTIONS)
        event.app.invalidate()

    @kb.add("enter", filter=permission)
    def _(event):
        action = PermissionCardComponent.ACTIONS[ui.approval_menu_index][0]
        asyncio.create_task(ui.resolve_approval(action))

    @kb.add("a", filter=permission)
    @kb.add("A", filter=permission)
    def _(event): asyncio.create_task(ui.resolve_approval("always_workspace"))

    @kb.add("s", filter=permission)
    def _(event): asyncio.create_task(ui.resolve_approval("always_session"))

    @kb.add("n", filter=permission)
    def _(event): asyncio.create_task(ui.resolve_approval("deny"))

    @kb.add("N", filter=permission)
    def _(event): asyncio.create_task(ui.resolve_approval("deny_all"))

    autonomy_cond = Condition(lambda: ui.state.active_overlay == "autonomy_control")

    @kb.add("1", filter=autonomy_cond)
    def _(event):
        asyncio.create_task(ui._set_autonomy_profile("autonomous"))

    @kb.add("2", filter=autonomy_cond)
    def _(event):
        asyncio.create_task(ui._set_autonomy_profile("supervised"))

    @kb.add("3", filter=autonomy_cond)
    def _(event):
        asyncio.create_task(ui._set_autonomy_profile("read_only"))

    @kb.add("r", filter=autonomy_cond)
    def _(event):
        asyncio.create_task(ui._clear_remembered_approvals("all"))

    @kb.add("escape", filter=autonomy_cond)
    def _(event):
        ui.close_overlay()

    # Notice popup dismissal keybindings
    has_toasts = Condition(lambda: bool(ui.state.active_toasts()))
    notice_popup = has_toasts & Condition(lambda: ui.state.active_overlay is None)
    @kb.add("escape", filter=notice_popup, eager=True)
    @kb.add("enter", filter=notice_popup, eager=True)
    def _(event):
        ui.state.clear_toasts()
        if ui.application: ui.application.invalidate()

    @kb.add("enter", filter=palette)
    def _(event): asyncio.create_task(ui._run_selected_palette())

    @kb.add("tab", filter=Condition(lambda: ui.state.active_overlay is not None and ui.state.active_overlay not in {"model_setup", "reverse_proxy"}))
    def _(event): event.app.layout.focus_next()

    @kb.add("s-tab", filter=Condition(lambda: ui.state.active_overlay is not None and ui.state.active_overlay not in {"model_setup", "reverse_proxy"}))
    def _(event): event.app.layout.focus_previous()

    ui.keymap.capture(kb)
    return kb
