"""Deterministic Priority-Driven Overlay Manager and Focus Coordinator."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from kitt.ui.app import KittUIApp


class OverlayPriority(IntEnum):
    BACKGROUND = 0
    PASSIVE = 100
    SIDEBAR = 200
    COMPLETION = 250
    HELP = 400
    NAVIGATION = 500
    INTERACTIVE = 650
    TRANSACTIONAL = 750
    CREDENTIAL_PICKER = 850
    AUTHENTICATION = 900
    SECURITY = 1000
    SYSTEM_CRITICAL = 1100


@dataclass(frozen=True)
class OverlaySpec:
    name: str
    priority: OverlayPriority
    modal: bool = True
    blocks_input_below: bool = True
    dismiss_policy: str = "close_to_parent"
    focus_policy: str = "preferred"
    parent_policy: str = "restore_parent"
    allow_duplicates: bool = False
    dim_background: bool = True
    esc_behavior: str = "close"


OVERLAY_SPECS: Dict[str, OverlaySpec] = {
    "permission": OverlaySpec(
        name="permission",
        priority=OverlayPriority.SECURITY,
        modal=True,
        blocks_input_below=True,
    ),
    "auth_login": OverlaySpec(
        name="auth_login",
        priority=OverlayPriority.AUTHENTICATION,
        modal=True,
        blocks_input_below=True,
    ),
    "model_setup": OverlaySpec(
        name="model_setup",
        priority=OverlayPriority.TRANSACTIONAL,
        modal=True,
        blocks_input_below=True,
    ),
    "provider_popup": OverlaySpec(
        name="provider_popup",
        priority=OverlayPriority.INTERACTIVE,
        modal=True,
        blocks_input_below=True,
    ),
    "add_provider": OverlaySpec(
        name="add_provider",
        priority=OverlayPriority.INTERACTIVE,
        modal=True,
        blocks_input_below=True,
    ),
    "provider_endpoint": OverlaySpec(
        name="provider_endpoint",
        priority=OverlayPriority.INTERACTIVE,
        modal=True,
        blocks_input_below=True,
    ),
    "palette": OverlaySpec(
        name="palette",
        priority=OverlayPriority.NAVIGATION,
        modal=True,
        blocks_input_below=True,
    ),
    "session_picker": OverlaySpec(
        name="session_picker",
        priority=OverlayPriority.NAVIGATION,
        modal=True,
        blocks_input_below=True,
    ),
    "timeline": OverlaySpec(
        name="timeline",
        priority=OverlayPriority.NAVIGATION,
        modal=True,
        blocks_input_below=True,
    ),
    "diff": OverlaySpec(
        name="diff",
        priority=OverlayPriority.NAVIGATION,
        modal=True,
        blocks_input_below=True,
    ),
    "agents": OverlaySpec(
        name="agents",
        priority=OverlayPriority.NAVIGATION,
        modal=True,
        blocks_input_below=True,
    ),
    "help": OverlaySpec(
        name="help",
        priority=OverlayPriority.HELP,
        modal=True,
        blocks_input_below=True,
    ),
}


@dataclass
class OverlayFrame:
    instance_id: str
    spec: OverlaySpec
    parent_instance_id: Optional[str] = None
    previous_focus: Any = None
    preferred_focus: Any = None
    opened_at: float = field(default_factory=time.time)
    payload: Dict[str, Any] = field(default_factory=dict)
    suspended: bool = False

    @property
    def name(self) -> str:
        return self.spec.name


class OverlayManager:
    """Manages priority-driven modal overlay hierarchy, suspension, and focus restoration."""

    def __init__(self, app: KittUIApp):
        self.app = app
        self.frames: List[OverlayFrame] = []

    def get_spec(self, name: str) -> OverlaySpec:
        return OVERLAY_SPECS.get(
            name,
            OverlaySpec(name=name, priority=OverlayPriority.INTERACTIVE, modal=True),
        )

    def top_frame(self) -> Optional[OverlayFrame]:
        active_frames = [f for f in self.frames if not f.suspended]
        return active_frames[-1] if active_frames else None

    def open(
        self,
        name: str,
        control: Any = None,
        payload: Optional[Dict[str, Any]] = None,
        parent_name: Optional[str] = None,
    ) -> None:
        spec = self.get_spec(name)

        # Duplicate suppression
        if not spec.allow_duplicates and any(f.spec.name == name and not f.suspended for f in self.frames):
            # Already active at top, re-focus
            if control and self.app.application:
                try:
                    self.app.application.layout.focus(control)
                except Exception:
                    pass
            return

        curr_focus = self.app.application.layout.current_control if self.app.application else None
        parent_id = None
        if self.frames:
            parent_frame = self.top_frame()
            if parent_frame:
                parent_id = parent_frame.instance_id
                # If opening higher priority overlay (e.g. permission over model_setup), suspend lower priority parent
                if spec.priority > parent_frame.spec.priority:
                    parent_frame.suspended = True

        frame = OverlayFrame(
            instance_id=str(uuid.uuid4())[:8],
            spec=spec,
            parent_instance_id=parent_id,
            previous_focus=curr_focus,
            preferred_focus=control,
            payload=payload or {},
        )

        self.frames.append(frame)
        self._sync_state()

        if self.app.application:
            self.app.application.invalidate()
            if control:
                try:
                    self.app.application.layout.focus(control)
                except ValueError:
                    try:
                        asyncio.get_running_loop().call_soon(self._focus_if_visible, control)
                    except RuntimeError:
                        pass

    def _focus_if_visible(self, control: Any) -> None:
        if not self.app.application:
            return
        try:
            self.app.application.layout.focus(control)
        except (ValueError, KeyError):
            pass

    def close(self) -> Optional[str]:
        if not self.frames:
            self._sync_state()
            return None

        top = self.frames.pop()
        was_permission = (top.spec.name == "permission")

        # Clean up denied permissions if permission modal is closed by cancel/Esc
        if was_permission and self.app.state.pending_approvals:
            for req in list(self.app.state.pending_approvals):
                try:
                    self.app.runtime.approval.deny(
                        req["approval_id"],
                        "Permission closed with escape/cancel",
                    )
                except Exception:
                    pass
            self.app.state.pending_approvals.clear()
            if self.app.bridge and self.app.bridge.is_active:
                asyncio.create_task(self.app.bridge.cancel("Permission cancelled"))

        # Unsuspend parent or next highest priority frame
        if self.frames:
            # Find and resume parent or top frame
            next_top = self.frames[-1]
            next_top.suspended = False

        self._sync_state()

        # Deterministic Focus Restoration
        if self.app.application:
            target = None
            active_top = self.top_frame()
            if active_top and active_top.preferred_focus:
                target = active_top.preferred_focus
            elif top.previous_focus:
                target = top.previous_focus
            else:
                target = getattr(self.app, "prompt_control", None)

            if target:
                try:
                    self.app.application.layout.focus(target)
                except (ValueError, KeyError):
                    try:
                        if hasattr(self.app, "prompt_control"):
                            self.app.application.layout.focus(self.app.prompt_control)
                    except Exception:
                        pass

            self.app.application.invalidate()

        return top.spec.name

    def close_all(self) -> None:
        while self.frames:
            self.close()

    def _sync_state(self) -> None:
        top = self.top_frame()
        self.app.state.active_overlay = top.spec.name if top else None
        self.app.state.overlay_stack = [f.spec.name for f in self.frames if not f.suspended]
        # Keep legacy app.focus_stack synchronized for backwards compatibility
        self.app.focus_stack = list(self.frames)

    def is_modal_active(self) -> bool:
        top = self.top_frame()
        return bool(top and top.spec.modal)

    def active_priority(self) -> OverlayPriority:
        top = self.top_frame()
        return top.spec.priority if top else OverlayPriority.BACKGROUND
