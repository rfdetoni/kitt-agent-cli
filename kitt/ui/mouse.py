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

