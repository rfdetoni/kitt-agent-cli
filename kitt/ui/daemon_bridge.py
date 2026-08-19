from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional
from kitt.daemon.client import DaemonClient
from kitt.daemon.protocol import DaemonEvent
from kitt.core.turn_events import (
    TurnStarted,
    FilterCompleted,
    ContextResolved,
    ModelSelected,
    ThinkingStarted,
    ThinkingCompleted,
    ToolCallProposed,
    ApprovalRequired,
    ToolStarted,
    ToolCompleted,
    EditApplied,
    TurnCompleted,
    TurnFailed,
    TurnCancelled,
    TurnBlocked,
)

logger = logging.getLogger(__name__)


def map_daemon_event_to_turn_event(event: DaemonEvent) -> Any:
    """Translate wire DaemonEvent into a local typed TurnEvent object for TUI reducer."""
    event_type = event.event_type
    p = event.payload or {}

    if event_type == "TurnStarted":
        return TurnStarted(prompt=p.get("prompt", p.get("text", "")), turn_id=p.get("turn_id", ""))
    elif event_type == "FilterCompleted":
        return FilterCompleted(
            task_type=p.get("task_type", ""),
            intent=p.get("intent", ""),
            confidence=p.get("confidence", 1.0),
            fidelity_passed=p.get("fidelity_passed", True),
            fallback_applied=p.get("fallback_applied", False),
        )
    elif event_type == "ContextResolved":
        return ContextResolved(resolved_count=p.get("resolved_count", 0))
    elif event_type == "ModelSelected":
        return ModelSelected(
            role=p.get("role", ""),
            profile_name=p.get("profile_name", ""),
            context_window=p.get("context_window", 8192),
            cost_tier=p.get("cost_tier", "standard"),
        )
    elif event_type == "ThinkingStarted":
        return ThinkingStarted()
    elif event_type == "ThinkingCompleted":
        return ThinkingCompleted(
            duration_ms=p.get("duration_ms", 0),
            tokens=p.get("tokens", 0),
            thought=p.get("thought", ""),
        )
    elif event_type == "ToolCallProposed":
        return ToolCallProposed(
            call_id=p.get("call_id", ""),
            tool_name=p.get("tool_name", ""),
            args=p.get("args", {}),
            risk_level=p.get("risk_level", "LOW"),
            progress_pct=p.get("progress_pct", 0),
            payload_tokens=p.get("payload_tokens", 0),
        )
    elif event_type == "ApprovalRequired":
        return ApprovalRequired(
            action_hash=p.get("action_hash", ""),
            tool_name=p.get("tool_name", ""),
            args=p.get("args", {}),
            reason=p.get("reason", "Action requires user approval"),
            risk_level=p.get("risk_level", "MEDIUM"),
        )
    elif event_type == "ToolStarted":
        return ToolStarted(
            call_id=p.get("call_id", ""),
            tool_name=p.get("tool_name", ""),
            args=p.get("args", {}),
        )
    elif event_type == "ToolCompleted":
        return ToolCompleted(
            call_id=p.get("call_id", ""),
            tool_name=p.get("tool_name", ""),
            success=p.get("success", True),
            output=p.get("output", ""),
            error=p.get("error"),
            duration_ms=p.get("duration_ms", 0),
            tokens_saved=p.get("tokens_saved", 0),
        )
    elif event_type == "EditApplied":
        return EditApplied(
            applied_files=p.get("applied_files", []),
            created_files=p.get("created_files", []),
            total_added_lines=p.get("total_added_lines", 0),
            total_removed_lines=p.get("total_removed_lines", 0),
            modified_files_count=p.get("modified_files_count", 0),
        )
    elif event_type == "TurnCompleted":
        return TurnCompleted(
            response=p.get("response", p.get("output", "")),
            edit_result=p.get("edit_result"),
        )
    elif event_type == "TurnFailed":
        return TurnFailed(
            error=p.get("error", "Unknown error"),
            failure_category=p.get("failure_category", "EXECUTION_ERROR"),
        )
    elif event_type == "TurnCancelled":
        return TurnCancelled(reason=p.get("reason", "Cancelled by user"))
    elif event_type == "TurnBlocked":
        return TurnBlocked(reason=p.get("reason", "Blocked by security policy"))

    return None


class DaemonUIBridge:
    """Bridge connecting Terminal UI to background DaemonServer over multiplexed IPC."""

    def __init__(
        self,
        workspace_dir: str,
        token: Optional[str] = None,
        event_sink: Optional[Callable[[Any], None]] = None,
    ):
        self.workspace_dir = workspace_dir
        self.token = token
        self.event_sink = event_sink
        self.client: Optional[DaemonClient] = None
        self.attached_session_id: Optional[str] = None
        self.last_sequence_id: int = 0
        self._connected = False

    async def connect(self) -> bool:
        """Establish IPC connection and authenticate with daemon."""
        self.client = DaemonClient(self.workspace_dir, self.token)
        ok = await self.client.connect()
        if ok:
            self._connected = True
        return ok

    async def create_session(self, title: str = "New Session") -> Optional[str]:
        """Request daemon to create a new session explicitly."""
        if not self.client:
            return None
        res = await self.client.send_request("create_session", {"title": title})
        if res.get("status") == "ok":
            return res.get("session_id")
        return None

    async def attach(self, session_id: str) -> bool:
        """Attach to session, receive historical event replay, and subscribe to live stream."""
        if not self.client:
            return False

        self.attached_session_id = session_id

        def _on_wire_event(event: DaemonEvent) -> None:
            self.last_sequence_id = max(self.last_sequence_id, event.sequence_id)
            if self.event_sink:
                turn_evt = map_daemon_event_to_turn_event(event)
                if turn_evt is not None:
                    self.event_sink(turn_evt)

        res = await self.client.attach(
            session_id,
            on_event=_on_wire_event,
            last_sequence=self.last_sequence_id,
        )
        past_events = res if isinstance(res, list) else res.get("events", [])

        for evt in past_events:
            self.last_sequence_id = max(self.last_sequence_id, evt.sequence_id)
            if self.event_sink:
                turn_evt = map_daemon_event_to_turn_event(evt)
                if turn_evt is not None:
                    self.event_sink(turn_evt)

        return True

    async def detach(self) -> None:
        """Detach from session cleanly without stopping daemon."""
        if self.client and self.attached_session_id:
            await self.client.detach()
            self.attached_session_id = None

    async def send_input(self, text: str) -> bool:
        """Submit a new turn prompt to the attached session on the daemon."""
        if not self.client or not self.attached_session_id:
            return False
        return await self.client.send_input(self.attached_session_id, text)

    async def cancel_turn(self, turn_id: str = "") -> bool:
        """Cancel an in-flight turn on the attached daemon session."""
        if not self.client or not self.attached_session_id:
            return False
        res = await self.client.send_request(
            "cancel_turn",
            {"session_id": self.attached_session_id, "turn_id": turn_id},
        )
        return res.get("status") == "ok"

    async def close(self) -> None:
        """Close IPC client connection cleanly without stopping daemon server."""
        if self.client:
            try:
                await self.detach()
            except Exception:
                pass
            await self.client.close()
            self._connected = False
            self.client = None
