from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Callable

from kitt.daemon.client import DaemonClient
from kitt.daemon.protocol import DaemonEvent


class DaemonGateway:
    """Remote-Web adapter over KITT's private authenticated daemon protocol."""

    def __init__(self, workspace_root: str) -> None:
        self.workspace_root = str(Path(workspace_root).resolve())

    async def _request_async(self, action: str, params: dict | None = None) -> dict:
        client = DaemonClient(workspace_root=self.workspace_root)
        try:
            if not await client.connect():
                raise ConnectionError("KITT daemon is not running or authentication failed")
            payload = {"workspace": self.workspace_root}
            if params:
                payload.update(params)
            response = await client.send_request(action, payload, timeout=30.0)
            if response.get("status") != "ok":
                raise RuntimeError(str(response.get("error") or f"Daemon action {action} failed"))
            return response
        finally:
            await client.close()

    def request(self, action: str, params: dict | None = None) -> dict:
        return asyncio.run(self._request_async(action, params))

    def list_sessions(self) -> dict:
        return self.request("list_sessions")

    def create_session(self, title: str) -> dict:
        return self.request("create_session", {"title": title})

    def get_session(self, session_id: str) -> dict:
        return self.request("get_session", {"session_id": session_id})

    def send_input(self, session_id: str, text: str, mode: str = "auto") -> dict:
        return self.request(
            "send_input",
            {"session_id": session_id, "text": text, "mode": mode},
        )

    def cancel_turn(self, session_id: str, turn_id: str) -> dict:
        return self.request(
            "cancel_turn", {"session_id": session_id, "turn_id": turn_id}
        )

    def approvals(self, session_id: str | None = None) -> dict:
        params = {"session_id": session_id} if session_id else {}
        return self.request("approval.list", params)

    def approve(self, approval_id: str, session_id: str | None = None) -> dict:
        params = {"approval_id": approval_id}
        if session_id:
            params["session_id"] = session_id
        return self.request("approval.approve", params)

    def deny(self, approval_id: str, session_id: str | None = None) -> dict:
        params = {"approval_id": approval_id}
        if session_id:
            params["session_id"] = session_id
        return self.request("approval.deny", params)

    def status(self) -> dict:
        return self.request("runtime.status")

    def extensions(self) -> dict:
        return self.request("extensions.status")

    def artifacts(self, session_id: str) -> dict:
        return self.request("artifact.list", {"session_id": session_id})

    def read_artifact(self, session_id: str, artifact_id: str, offset: int = 0) -> dict:
        return self.request(
            "artifact.read",
            {"session_id": session_id, "artifact_id": artifact_id, "offset": max(0, int(offset))},
        )

    def workspace_diff(self) -> dict:
        return self.request("workspace.diff")

    def stream_events(
        self,
        session_id: str,
        last_sequence: int,
        emit: Callable[[DaemonEvent], None],
        heartbeat: Callable[[], None],
        stop: threading.Event,
    ) -> None:
        asyncio.run(
            self._stream_events_async(
                session_id=session_id,
                last_sequence=max(0, int(last_sequence)),
                emit=emit,
                heartbeat=heartbeat,
                stop=stop,
            )
        )

    async def _stream_events_async(
        self,
        session_id: str,
        last_sequence: int,
        emit: Callable[[DaemonEvent], None],
        heartbeat: Callable[[], None],
        stop: threading.Event,
    ) -> None:
        client = DaemonClient(workspace_root=self.workspace_root)
        pending_live: list[DaemonEvent] = []
        attached = False
        emitted: set[int] = set()

        def emit_once(evt: DaemonEvent) -> None:
            sequence = int(evt.sequence_id)
            if sequence in emitted:
                return
            emitted.add(sequence)
            # Bound the dedupe set. Sequence ids are monotonic, so retaining the
            # most recent ids is enough to eliminate reconnect/attach races.
            if len(emitted) > 4096:
                floor = max(emitted) - 2048
                emitted.intersection_update({value for value in emitted if value >= floor})
            emit(evt)

        def on_live(evt: DaemonEvent) -> None:
            if not attached:
                pending_live.append(evt)
                return
            emit_once(evt)

        try:
            if not await client.connect():
                raise ConnectionError("KITT daemon is not running or authentication failed")

            cursor = last_sequence
            # Catch up without subscribing first. This prevents the daemon's
            # bounded attach replay from leaving a large historical gap.
            while not stop.is_set():
                page = await client.send_request(
                    "events_since",
                    {
                        "workspace": self.workspace_root,
                        "session_id": session_id,
                        "last_sequence": cursor,
                        "limit": 200,
                    },
                    timeout=30.0,
                )
                if page.get("status") != "ok":
                    raise RuntimeError(str(page.get("error") or "Event replay failed"))
                for raw in page.get("events", []):
                    evt = DaemonEvent.from_dict(raw) if isinstance(raw, dict) else raw
                    emit_once(evt)
                    cursor = max(cursor, int(evt.sequence_id))
                if not page.get("has_more"):
                    break
                cursor = max(cursor, int(page.get("next_sequence") or cursor))

            attach = await client.attach(
                session_id,
                last_sequence=cursor,
                on_event=on_live,
                workspace=self.workspace_root,
            )
            if attach.get("status") != "ok":
                raise RuntimeError(str(attach.get("error") or "Session attach failed"))
            for evt in attach.get("events", []):
                emit_once(evt)
            attached = True
            for evt in sorted(pending_live, key=lambda item: int(item.sequence_id)):
                emit_once(evt)
            pending_live.clear()

            while not stop.is_set() and client._connected:
                await asyncio.sleep(15.0)
                if not stop.is_set():
                    heartbeat()
        finally:
            try:
                await client.close()
            except Exception:
                pass
