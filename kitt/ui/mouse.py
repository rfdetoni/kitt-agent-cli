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
