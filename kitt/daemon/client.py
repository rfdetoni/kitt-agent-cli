from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from kitt.daemon.protocol import DaemonEvent, decode_line, encode_message
from kitt.daemon.transport import IPCTransport


class DaemonClient:
    def __init__(self, workspace_root=None, socket_path=None, token_path=None, token=None):
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.transport = IPCTransport(self.workspace_root)
        self.socket_path = Path(socket_path) if socket_path else self.transport.socket_path
        self.token_path = Path(token_path) if token_path else (self.transport.kitt_dir / "daemon.token")
        self.token_override = token
        self.reader = None
        self.writer = None
        self._connected = False
        self._reader_task = None
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._event_callback: Optional[Callable[[DaemonEvent], None]] = None
        self.resync_required = False

    async def connect(self) -> bool:
        endpoint = self.transport.read_endpoint_metadata()
        if not endpoint and not self.socket_path.exists():
            return False
        try:
            token = self.token_override or self.transport.read_secret(self.token_path)
        except Exception:
            return False
        if not token:
            return False
        try:
            if endpoint and endpoint.transport_type == "tcp" and endpoint.port:
                self.reader, self.writer = await asyncio.open_connection(endpoint.address, endpoint.port)
            elif sys.platform != "win32":
                target = Path(endpoint.address) if endpoint else self.socket_path
                self.reader, self.writer = await asyncio.open_unix_connection(str(target))
            else:
                if not endpoint or not endpoint.port:
                    return False
                self.reader, self.writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
            self._connected = True
            self._reader_task = asyncio.create_task(self._reader_loop())
            resp = await self._send_request({"action": "auth", "token": token})
            if resp.get("status") == "ok":
                return True
        except Exception:
            pass
        await self.close()
        return False

    async def _reader_loop(self) -> None:
        try:
            while self._connected and self.reader:
                line = await self.reader.readline()
                if not line:
                    break
                try:
                    msg = decode_line(line)
                except Exception:
                    continue
                if msg.get("type") == "RESYNC_REQUIRED":
                    self.resync_required = True
                    continue
                if msg.get("type") == "EVENT" and "event" in msg:
                    evt = DaemonEvent.from_dict(msg["event"])
                    if self._event_callback:
                        self._event_callback(evt)
                    continue
                req_id = msg.get("request_id")
                if req_id and req_id in self._pending_requests:
                    fut = self._pending_requests[req_id]
                    if not fut.done():
                        fut.set_result(msg)
        except asyncio.CancelledError:
            pass
        finally:
            self._connected = False
            for fut in tuple(self._pending_requests.values()):
                if not fut.done():
                    fut.cancel()

    async def _send_request(self, req: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
        if not self.writer or not self._connected:
            raise ConnectionError("Not connected to daemon")
        req_id = req.get("request_id") or f"req_{uuid.uuid4().hex}"
        payload = dict(req, request_id=req_id, type="REQUEST")
        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut
        try:
            self.writer.write(encode_message(payload))
            await self.writer.drain()
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending_requests.pop(req_id, None)

    async def send_request(self, action: str, params=None, timeout: float = 15.0):
        payload = {"action": action}
        if params:
            payload.update(params)
        return await self._send_request(payload, timeout=timeout)

    async def is_running(self) -> bool:
        if not self._connected and not await self.connect():
            return False
        try:
            return (await self._send_request({"action": "ping"})).get("status") == "ok"
        except Exception:
            return False

    async def list_sessions(self, workspace=None):
        return await self._send_request({"action": "list_sessions", "workspace": workspace})

    async def create_session(self, title: str = "New Session", workspace: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_request({"action": "create_session", "title": title, "workspace": workspace})

    async def attach(self, session_id, last_sequence=0, event_callback=None, on_event=None, workspace=None):
        self._event_callback = on_event or event_callback
        res = await self._send_request({
            "action": "attach", "session_id": session_id,
            "last_sequence": last_sequence, "workspace": workspace,
        })
        res["events"] = [
            DaemonEvent.from_dict(e) if isinstance(e, dict) else e
            for e in res.get("events", [])
        ]
        return res

    async def detach(self):
        res = await self._send_request({"action": "detach"})
        self._event_callback = None
        return res

    async def submit_turn(self, session_id, text, mode="auto", explicit_files=None,
                          no_history=False, workspace=None):
        return await self._send_request({
            "action": "send_input",
            "session_id": session_id,
            "text": text,
            "mode": mode,
            "explicit_files": list(explicit_files or ()),
            "no_history": bool(no_history),
            "workspace": workspace,
        })

    async def send_input(self, session_id, text, workspace=None):
        return (await self.submit_turn(session_id, text, workspace=workspace)).get("status") == "ok"

    async def continue_turn(self, session_id, grant, workspace=None):
        payload = {
            "approval_id": grant.approval_id,
            "turn_id": grant.turn_id,
            "conversation_id": grant.conversation_id,
            "workspace_id": grant.workspace_id,
            "action_hash": grant.action_hash,
            "granted_at": grant.granted_at,
            "expires_at": grant.expires_at,
            "nonce": grant.nonce,
        }
        return await self._send_request({
            "action": "continue_turn", "session_id": session_id,
            "grant": payload, "workspace": workspace,
        })

    async def stop_daemon(self):
        return await self._send_request({"action": "stop"})

    async def close(self):
        self._connected = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        self.reader = None
        self.writer = None
