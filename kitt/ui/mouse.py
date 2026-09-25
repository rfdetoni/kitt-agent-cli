from __future__ import annotations

import asyncio
from typing import Any

from prompt_toolkit.mouse_events import MouseEventType


def model_setup_mouse_handler(ui, mouse_event) -> Any:
    """Handle hover/click only; wheel routing belongs to kitt.ui.scroll."""
    if mouse_event.event_type == MouseEventType.MOUSE_MOVE:
        ui.model_setup_model.handle_mouse_hover(mouse_event.position.y)
        if ui.application:
            ui.application.invalidate()
        return None
    if mouse_event.event_type == MouseEventType.MOUSE_UP:
        ui.model_setup_model.handle_mouse_hover(mouse_event.position.y)
        asyncio.create_task(ui._apply_selected_model())
        return None
    return NotImplemented


def provider_popup_mouse_handler(ui, mouse_event) -> Any:
    """Handle provider hover/click while the centralized scroll router owns the wheel."""
    if mouse_event.event_type == MouseEventType.MOUSE_MOVE:
        ui.model_setup_model.handle_popup_mouse_hover(mouse_event.position.y)
        if ui.application:
            ui.application.invalidate()
        return None
    if mouse_event.event_type == MouseEventType.MOUSE_UP:
        ui.model_setup_model.handle_popup_mouse_hover(mouse_event.position.y)
        entry = ui.model_setup_model.get_selected_popup_entry()
        if entry:
            if entry["kind"] == "action":
                ui._select_popup_action(entry)
            elif entry["kind"] == "provider":
                provider = entry["name"]
                if provider in ui.model_setup_model.providers:
                    ui.model_setup_model.provider_index = ui.model_setup_model.providers.index(provider)
                ui.close_overlay()
                asyncio.create_task(ui._prepare_model_setup())
        return None
    return NotImplemented


def _scroll_transcript(ui, delta: int) -> None:
    if not hasattr(ui, "transcript_window"):
        return
    window = ui.transcript_window
    if delta < 0:
        ui.state.follow_tail = False
        window.vertical_scroll = max(0, window.vertical_scroll + delta)
    else:
        info = getattr(window, "render_info", None)
        if info is not None and info.bottom_visible:
            ui.state.follow_tail = True
            ui.state.unseen_output = False
            window.vertical_scroll = 10**9
        else:
            window.vertical_scroll += delta
    if ui.application:
        ui.application.invalidate()


def toggle_mouse_support(ui) -> bool:
    ui.mouse_support_enabled = not getattr(ui, "mouse_support_enabled", True)
    ui.state.mouse_enabled = ui.mouse_support_enabled
    if ui.application and hasattr(ui.application, "output"):
        try:
            if ui.mouse_support_enabled:
                ui.application.output.enable_mouse_support()
            else:
                ui.application.output.disable_mouse_support()
        except Exception:
            pass
    msg = "Mouse TUI ativado (Scroll Interativo)" if ui.mouse_support_enabled else "Mouse Terminal Nativo (Seleção/Cópia de Texto Habilitada)"
    ui.state.add_toast(msg)
    if ui.application:
        ui.application.invalidate()
    return ui.mouse_support_enabled



def _focus_surface(ui, surface: str) -> None:
    if not ui.application:
        return
    controls = {
        "palette": getattr(ui, "palette_control", None),
        "session_picker": getattr(ui, "session_picker_control", None),
        "timeline": getattr(ui, "timeline_control", None),
        "diff": getattr(ui, "diff_control", None),
        "agents": getattr(ui, "agents_control", None),
        "permission": getattr(ui, "permission_control", None),
        "autonomy": getattr(ui, "autonomy_control", None),
        "reverse_proxy": getattr(ui, "reverse_proxy_control", None),
        "help": getattr(ui, "help_control", None),
        "model_setup_header": getattr(ui, "model_setup_search_control", None),
    }
    target = controls.get(surface)
    if target is not None:
        try:
            ui.application.layout.focus(target)
        except (ValueError, KeyError):
            pass


def _preview_interaction(ui, surface: str, region) -> None:
    if region is None:
        return
    if region.action == "palette.item":
        ui.palette_index = int(region.value)
    elif region.action == "session.item":
        ui.session_picker_model.selected_index = int(region.value)
    elif region.action == "timeline.item":
        ui.timeline_model.selected_index = int(region.value)
    elif region.action == "permission.action":
        ui.approval_menu_index = int(region.value[0])
    elif region.action == "reverse_proxy.item":
        ui.reverse_proxy_model.selected_index = int(region.value)
    if ui.application:
        ui.application.invalidate()


def _activate_interaction(ui, region) -> None:
    if region is None:
        return
    action = region.action
    value = region.value

    if action == "palette.item":
        asyncio.create_task(ui._run_selected_palette())
    elif action == "session.item":
        selected = ui.session_picker_model.get_selected()
        if selected:
            ui.close_overlay()
            asyncio.create_task(ui._execute_command(f"/resume {selected['id']}"))
    elif action == "permission.action":
        asyncio.create_task(ui.resolve_approval(str(value[1])))
    elif action == "autonomy.profile":
        asyncio.create_task(ui._set_autonomy_profile(str(value)))
    elif action == "autonomy.clear":
        asyncio.create_task(ui._clear_remembered_approvals("all"))
    elif action == "reverse_proxy.show":
        ui._reverse_proxy_show(str(value))
    elif action == "reverse_proxy.refresh":
        asyncio.create_task(ui._refresh_reverse_proxy())
    elif action == "reverse_proxy.restart":
        asyncio.create_task(ui._reverse_proxy_restart_selected())
    elif action == "reverse_proxy.stop":
        asyncio.create_task(ui._reverse_proxy_stop_selected())
    elif action == "reverse_proxy.bind":
        asyncio.create_task(ui._reverse_proxy_bind_selected(str(value)))
    elif action == "reverse_proxy.start":
        asyncio.create_task(ui._reverse_proxy_start_selected())
    elif action == "reverse_proxy.profile_next":
        ui._reverse_proxy_cycle_profile(1)
    elif action == "reverse_proxy.url":
        ui._reverse_proxy_prepare_url()
    elif action == "reverse_proxy.profile_create":
        ui._reverse_proxy_prepare_profile_create()
    elif action == "reverse_proxy.profile_remove":
        asyncio.create_task(ui._reverse_proxy_remove_profile())
    elif action == "model.role_next":
        asyncio.create_task(ui._move_model_role(1))
    elif action == "model.role":
        target = int(value)
        current = int(ui.model_setup_model.role_index)
        asyncio.create_task(ui._move_model_role(target - current))
    elif action == "model.limits":
        asyncio.create_task(
            ui._toggle_role_local_limits(ui.model_setup_model.selected_role)
        )
    elif action == "model.providers":
        ui._open_provider_popup_overlay()
    elif action == "model.auth":
        ui._open_auth_login_overlay(
            ui.model_setup_model.selected_provider,
            parent_name="model_setup",
        )
    elif action == "model.apply":
        asyncio.create_task(ui._apply_selected_model())


def interactive_surface_mouse_handler(ui, surface: str, mouse_event) -> Any:
    """Route local-cell mouse input through the semantic interaction map."""
    event_type = mouse_event.event_type
    position = getattr(mouse_event, "position", None)
    if position is None:
        return NotImplemented

    if event_type == MouseEventType.MOUSE_MOVE:
        region = ui.interactions.hover(surface, position.x, position.y)
        _preview_interaction(ui, surface, region)
        return None if region is not None else NotImplemented

    if event_type == MouseEventType.MOUSE_DOWN:
        _focus_surface(ui, surface)
        # Keep the rendered geometry stable until release. Updating selection
        # on press can virtualize the list and move the target before MOUSE_UP.
        region = ui.interactions.press(surface, position.x, position.y)
        return None if region is not None else NotImplemented

    if event_type == MouseEventType.MOUSE_UP:
        region = ui.interactions.release(surface, position.x, position.y)
        _preview_interaction(ui, surface, region)
        _activate_interaction(ui, region)
        return None if region is not None else NotImplemented

    return NotImplemented
