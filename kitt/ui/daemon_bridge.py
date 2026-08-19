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
        # ContextResolved/ContextBuildCompleted carry the useful UI telemetry.
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
        self.last_sequence_id = 0
        self._connected = False

    async def connect(self) -> bool:
        self.client = DaemonClient(self.workspace_dir, token=self.token)
        self._connected = await self.client.connect()
        return self._connected

    async def create_session(self, title: str = "New Session") -> Optional[str]:
        if not self.client:
            return None
        res = await self.client.send_request("create_session", {"title": title})
        return res.get("session_id") if res.get("status") == "ok" else None

    def _on_wire_event(self, event: DaemonEvent) -> None:
        self.last_sequence_id = max(self.last_sequence_id, event.sequence_id)
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
            last_sequence=self.last_sequence_id,
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
        if not self.client or not self.attached_session_id:
            return None
        res = await self.client.submit_turn(
            self.attached_session_id,
            text,
            mode=mode,
            explicit_files=list(explicit_files or ()),
            no_history=no_history,
        )
        return res.get("turn_id") if res.get("status") == "ok" else None

    async def send_input(self, text: str) -> bool:
        return bool(await self.submit_turn(text))

    async def continue_turn(self, grant) -> bool:
        if not self.client or not self.attached_session_id:
            return False
        res = await self.client.continue_turn(self.attached_session_id, grant)
        return res.get("status") == "ok"

    async def cancel_turn(self, turn_id: str = "") -> bool:
        if not self.client or not self.attached_session_id:
            return False
        res = await self.client.send_request(
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
