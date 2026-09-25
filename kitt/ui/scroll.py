from __future__ import annotations

from collections.abc import Callable
from typing import Any

from prompt_toolkit.mouse_events import MouseEventType


MouseHandler = Callable[[Any], Any]


def compose_mouse_handlers(*handlers: MouseHandler | None) -> MouseHandler:
    """Compose mouse handlers without duplicating wheel-routing logic."""
    active = tuple(handler for handler in handlers if handler is not None)

    def handle(mouse_event):
        for handler in active:
            result = handler(mouse_event)
            if result is not NotImplemented:
                return result
        return NotImplemented

    return handle


def make_wheel_scroll_handler(
    get_window: Callable[[], Any],
    *,
    step: int = 3,
    min_scroll: int = 0,
    invalidate: Callable[[], None] | None = None,
    after_scroll: Callable[[Any, Any], None] | None = None,
) -> MouseHandler:
    """Create isolated vertical wheel scrolling for one retained Window."""
    step = max(1, int(step))

    def handle(mouse_event):
        event_type = mouse_event.event_type
        if event_type not in {MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN}:
            return NotImplemented
        window = get_window()
        if window is None:
            return None
        if event_type == MouseEventType.SCROLL_UP:
            window.vertical_scroll = max(min_scroll, int(window.vertical_scroll) - step)
        else:
            window.vertical_scroll = int(window.vertical_scroll) + step
        if after_scroll is not None:
            after_scroll(event_type, window)
        if invalidate is not None:
            invalidate()
        return None

    return handle


def make_index_wheel_handler(
    move: Callable[[int], None],
    *,
    step: int = 1,
    invalidate: Callable[[], None] | None = None,
) -> MouseHandler:
    """Wheel adapter for virtualized selectors whose scroll is their selected index."""
    step = max(1, int(step))

    def handle(mouse_event):
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            move(-step)
        elif mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            move(step)
        else:
            return NotImplemented
        if invalidate is not None:
            invalidate()
        return None

    return handle


def register_scrollable_window(
    ui,
    name: str,
    window,
    *,
    control=None,
    wheel_handler: MouseHandler | None = None,
    step: int = 3,
    after_scroll: Callable[[Any, Any], None] | None = None,
):
    """Register a scroll surface and install one isolated wheel route on its control."""
    ui.scrollable_windows[name] = window
    target_control = control if control is not None else getattr(window, "content", None)
    if target_control is None:
        return window

    invalidate = lambda: ui.application.invalidate() if ui.application else None
    wheel = wheel_handler or make_wheel_scroll_handler(
        lambda: ui.scrollable_windows.get(name),
        step=step,
        invalidate=invalidate,
        after_scroll=after_scroll,
    )
    existing = getattr(target_control, "mouse_handler", None)
    target_control.mouse_handler = compose_mouse_handlers(wheel, existing)
    return window
