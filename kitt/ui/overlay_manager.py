"""Overlay state and focus stack manager for K.I.T.T. UI."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional
from kitt.ui.overlay_models import OverlayFrame

if TYPE_CHECKING:
    from kitt.ui.app import KittUIApp


class OverlayManager:
    """Manages modal overlay activation, focus stack, and dismissal."""

    def __init__(self, app: KittUIApp):
        self.app = app

    def open(self, name: str, control=None) -> None:
        curr_focus = self.app.application.layout.current_control if self.app.application else None
        frame = OverlayFrame(name=name, previous_focus=curr_focus, preferred_focus=control)
        self.app.focus_stack.append(frame)
        self.app.state.push_overlay(name)
        if self.app.application:
            self.app.application.invalidate()
            if control:
                try:
                    self.app.application.layout.focus(control)
                except ValueError:
                    asyncio.get_running_loop().call_soon(self._focus_if_visible, control)

    def _focus_if_visible(self, control) -> None:
        if not self.app.application:
            return
        try:
            self.app.application.layout.focus(control)
        except ValueError:
            pass

    def close(self) -> None:
        was_permission = (self.app.state.active_overlay == "permission")
        self.app.state.pop_overlay()
        frame = self.app.focus_stack.pop() if self.app.focus_stack else None
        if was_permission and self.app.state.pending_approvals:
            for req in list(self.app.state.pending_approvals):
                try:
                    self.app.runtime.approval.deny(req["approval_id"], "Permission closed with escape/cancel")
                except Exception:
                    pass
            self.app.state.pending_approvals.clear()
            if self.app.bridge and self.app.bridge.is_active:
                asyncio.create_task(self.app.bridge.cancel("Permission cancelled"))
        if self.app.application:
            target = None
            if self.app.focus_stack and self.app.focus_stack[-1].preferred_focus:
                target = self.app.focus_stack[-1].preferred_focus
            elif frame and frame.previous_focus:
                target = frame.previous_focus
            else:
                target = getattr(self.app, "prompt_control", None)

            if target:
                try:
                    self.app.application.layout.focus(target)
                except ValueError:
                    try:
                        if hasattr(self.app, "prompt_control"):
                            self.app.application.layout.focus(self.app.prompt_control)
                    except ValueError:
                        pass
            self.app.application.invalidate()
