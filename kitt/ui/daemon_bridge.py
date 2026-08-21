from __future__ import annotations

from dataclasses import fields
from typing import Any, Callable, Optional

from kitt.daemon.client import DaemonClient
from kitt.daemon.protocol import DaemonEvent
from kitt.core import turn_events as te


_EVENT_TYPES = {
    name: getattr(te, name)
    for name in (
        "TurnStarted", "FilterCompleted", "ContextResolved", "ContextBuildCompleted",
        "BudgetApplied", "ModelSelected", "TextDelta", "ThinkingStarted",
        "ThinkingCompleted", "ToolCallProposed", "ApprovalRequired", "ToolStarted",
        "ToolCompleted", "EditApplied", "MetricsRecorded", "TurnCompleted",
        "TurnFailed", "TurnCancelled", "TurnBlocked", "ChildAgentSpawned",
        "ChildAgentProgress", "ChildAgentFinished",
    )
    if hasattr(te, name)
}


def map_daemon_event_to_turn_event(event: DaemonEvent) -> Any:
    cls = _EVENT_TYPES.get(event.event_type)
    if cls is None:
        return None
    if cls is te.FilterCompleted:
        # The semantic filter object is intentionally not rehydrated across IPC.
        return te.FilterCompleted(filter_res=None)
    payload = event.payload or {}
    allowed = {f.name for f in fields(cls) if f.init and f.name != "timestamp"}
    kwargs = {k: v for k, v in payload.items() if k in allowed}
    try:
        return cls(**kwargs)
    except (TypeError, ValueError):
        # Never allow a schema-version mismatch to break the TUI stream.
        return cls()


class DaemonUIBridge:
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
        self._last_sequence_by_session: dict[str, int] = {}
        self._connected = False

    @property
    def last_sequence_id(self) -> int:
        sid = self.attached_session_id or ""
        return self._last_sequence_by_session.get(sid, 0)

    async def connect(self) -> bool:
        self.client = DaemonClient(self.workspace_dir, token=self.token)
        self._connected = await self.client.connect()
        return self._connected

    async def request(self, action: str, params: Optional[dict] = None, timeout: float = 30.0) -> dict:
        if not self.client:
            raise RuntimeError("Daemon UI bridge is not connected")
        payload = {"workspace": self.workspace_dir}
        if params:
            payload.update(params)
        response = await self.client.send_request(action, payload, timeout=timeout)
        if response.get("status") != "ok":
            raise RuntimeError(str(response.get("error") or f"Daemon action {action} failed"))
        return response

    async def create_session(self, title: str = "New Session") -> Optional[str]:
        res = await self.request("create_session", {"title": title})
        return res.get("session_id")

    def _on_wire_event(self, event: DaemonEvent) -> None:
        sid = str(event.session_id or self.attached_session_id or "")
        if sid:
            self._last_sequence_by_session[sid] = max(
                self._last_sequence_by_session.get(sid, 0), event.sequence_id
            )
        if self.event_sink:
            mapped = map_daemon_event_to_turn_event(event)
            if mapped is not None:
                self.event_sink(mapped)

    async def attach(self, session_id: str) -> bool:
        if not self.client:
            return False
        res = await self.client.attach(
            session_id,
            on_event=self._on_wire_event,
            last_sequence=self._last_sequence_by_session.get(session_id, 0),
        )
        if res.get("status") != "ok":
            return False
        self.attached_session_id = session_id
        for evt in res.get("events", []):
            self._on_wire_event(evt)
        while res.get("has_more"):
            res = await self.client.attach(
                session_id,
                on_event=self._on_wire_event,
                last_sequence=int(res.get("next_sequence", self.last_sequence_id)),
            )
            if res.get("status") != "ok":
                return False
            for evt in res.get("events", []):
                self._on_wire_event(evt)
        return True

    async def detach(self) -> None:
        if self.client and self.attached_session_id:
            await self.client.detach()
        self.attached_session_id = None

    async def submit_turn(
        self, text: str, *, mode: str = "auto",
        explicit_files=None, no_history: bool = False,
    ) -> Optional[str]:
        if not self.attached_session_id:
            return None
        res = await self.request(
            "send_input",
            {
                "session_id": self.attached_session_id,
                "text": text,
                "mode": mode,
                "explicit_files": list(explicit_files or ()),
                "no_history": no_history,
            },
        )
        return res.get("turn_id")

    async def send_input(self, text: str) -> bool:
        return bool(await self.submit_turn(text))

    async def continue_turn(self, grant) -> bool:
        # Compatibility path for explicitly local approval brokers. Normal TUI
        # approval uses approval_action() so the grant nonce never leaves daemon.
        if not self.attached_session_id:
            return False
        res = await self.request(
            "continue_turn",
            {
                "session_id": self.attached_session_id,
                "grant": {
                    "approval_id": grant.approval_id,
                    "turn_id": grant.turn_id,
                    "conversation_id": grant.conversation_id,
                    "workspace_id": grant.workspace_id,
                    "action_hash": grant.action_hash,
                    "granted_at": grant.granted_at,
                    "expires_at": grant.expires_at,
                    "nonce": grant.nonce,
                },
            },
        )
        return res.get("status") == "ok"

    async def approval_action(self, approval_id: str, allow: bool) -> dict:
        if not self.attached_session_id:
            raise RuntimeError("No daemon session is attached")
        return await self.request(
            "approval.approve" if allow else "approval.deny",
            {
                "approval_id": approval_id,
                "session_id": self.attached_session_id,
            },
        )

    async def list_approvals(self) -> list[dict]:
        params = {"session_id": self.attached_session_id} if self.attached_session_id else {}
        return (await self.request("approval.list", params)).get("approvals", [])

    async def remember_approval(self, tool_name: str, scope: str) -> dict:
        if scope == "session" and not self.attached_session_id:
            raise RuntimeError("Session-scoped approval requires an attached daemon session")
        return await self.request(
            "approval.remember",
            {
                "tool_name": tool_name,
                "scope": scope,
                "decision": "allow",
                "path_glob": "**",
                "session_id": self.attached_session_id or "",
            },
        )

    async def clear_remembered(self, scope: str = "session") -> dict:
        if scope == "session" and not self.attached_session_id:
            raise RuntimeError("Session-scoped clear requires an attached daemon session")
        return await self.request(
            "approval.clear_remembered",
            {"scope": scope, "session_id": self.attached_session_id or ""},
        )

    async def execute_direct_tool(self, tool_name: str, args: dict) -> dict:
        if not self.attached_session_id:
            raise RuntimeError("No daemon session is attached")
        return await self.request(
            "ui.tool.execute",
            {
                "session_id": self.attached_session_id,
                "tool_name": tool_name,
                "args": dict(args or {}),
            },
            timeout=180.0,
        )

    async def undo(self) -> dict:
        return await self.request("ui.undo", {"session_id": self.attached_session_id or ""})

    async def set_reasoning(self, value: int) -> dict:
        return await self.request("runtime.set_reasoning", {"value": max(0, min(100, int(value)))})

    async def set_autonomy(self, preset: str) -> dict:
        return await self.request("runtime.set_autonomy", {"preset": str(preset)})

    async def reload_router(self) -> dict:
        return await self.request("runtime.reload_router")

    async def cancel_turn(self, turn_id: str = "") -> bool:
        if not self.attached_session_id:
            return False
        res = await self.request(
            "cancel_turn",
            {"session_id": self.attached_session_id, "turn_id": turn_id},
        )
        return res.get("status") == "ok"

    async def close(self) -> None:
        if self.client:
            try:
                await self.detach()
            finally:
                await self.client.close()
        self.client = None
        self._connected = False
