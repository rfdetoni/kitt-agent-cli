from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from kitt.daemon.protocol import DaemonEvent, decode_line, encode_message
from kitt.daemon.server import get_default_socket_path, get_default_token_path
from kitt.daemon.transport import IPCTransport


class DaemonClient:
    """Client for attaching to and interacting with a running KITT Daemon over multiplexed IPC."""

    def __init__(
        self,
        workspace_root: Optional[Path | str] = None,
        socket_path: Optional[Path | str] = None,
        token_path: Optional[Path | str] = None,
        token: Optional[str] = None,
    ):
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.transport = IPCTransport(self.workspace_root)
        self.socket_path = Path(socket_path) if socket_path else self.transport.socket_path
        self.token_path = Path(token_path) if token_path else (self.transport.kitt_dir / "daemon.token")
        self.token_override = token
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._reader_task: Optional[asyncio.Task] = None
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._event_callback: Optional[Callable[[DaemonEvent], None]] = None

    async def connect(self) -> bool:
        """Connect to daemon, authenticate, and start single multiplexed reader loop."""
        endpoint = self.transport.read_endpoint_metadata()
        if not endpoint and not self.socket_path.exists():
            return False

        token = self.token_override or (self.token_path.read_text(encoding="utf-8").strip() if self.token_path.exists() else "")
        if not token:
            return False

        try:
            if endpoint and endpoint.transport_type == "tcp" and endpoint.port:
                self.reader, self.writer = await asyncio.open_connection(endpoint.address, endpoint.port)
            elif sys.platform != "win32":
                target_sock = Path(endpoint.address) if endpoint else self.socket_path
                self.reader, self.writer = await asyncio.open_unix_connection(str(target_sock))
            else:
                self.reader, self.writer = await asyncio.open_connection("127.0.0.1", endpoint.port if endpoint else 0)

            # Start background multiplexed reader loop
            self._connected = True
            self._reader_task = asyncio.create_task(self._reader_loop())

            # Send auth request over multiplexed channel
            resp = await self._send_request({"action": "auth", "token": token})
            if resp.get("status") == "ok":
                return True
            await self.close()
            return False
        except Exception:
            await self.close()
            return False

    async def _reader_loop(self) -> None:
        """Single reader loop for all multiplexed requests and streamed events."""
        try:
            while self._connected and self.reader:
                line = await self.reader.readline()
                if not line:
                    break
                try:
                    msg = decode_line(line)
                except Exception:
                    continue

                msg_type = msg.get("type")
                if msg_type == "RESPONSE" or "request_id" in msg:
                    req_id = msg.get("request_id")
                    if req_id and req_id in self._pending_requests:
                        fut = self._pending_requests[req_id]
                        if not fut.done():
                            fut.set_result(msg)
                elif msg_type == "EVENT" and "event" in msg:
                    evt = DaemonEvent.from_dict(msg["event"])
                    if self._event_callback:
                        self._event_callback(evt)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            self._connected = False
            # Cancel all pending futures
            for fut in self._pending_requests.values():
                if not fut.done():
                    fut.cancel()
            self._pending_requests.clear()

    async def is_running(self) -> bool:
        """Check if daemon is reachable."""
        try:
            if not self._connected:
                ok = await self.connect()
                if not ok:
                    return False
            resp = await self._send_request({"action": "ping"})
            return resp.get("status") == "ok"
        except Exception:
            return False

    async def _send_request(self, req: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
        if not self.writer or not self.reader or not self._connected:
            raise ConnectionError("Not connected to daemon")

        req_id = req.get("request_id") or f"req_{uuid.uuid4().hex}"
        req_copy = dict(req)
        req_copy["request_id"] = req_id
        req_copy["type"] = "REQUEST"

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut

        try:
            self.writer.write(encode_message(req_copy))
            await self.writer.drain()
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending_requests.pop(req_id, None)

    async def send_request(self, action: str, params: Optional[Dict[str, Any]] = None, timeout: float = 15.0) -> Dict[str, Any]:
        payload = {"action": action}
        if params:
            payload.update(params)
        return await self._send_request(payload, timeout=timeout)

    async def list_sessions(self, workspace: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_request({"action": "list_sessions", "workspace": workspace})

    async def attach(
        self,
        session_id: str,
        last_sequence: int = 0,
        event_callback: Optional[Callable[[DaemonEvent], None]] = None,
        on_event: Optional[Callable[[DaemonEvent], None]] = None,
        workspace: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._event_callback = on_event or event_callback
        resp = await self._send_request({
            "action": "attach",
            "session_id": session_id,
            "last_sequence": last_sequence,
            "workspace": workspace,
        })
        raw_events = resp.get("events", [])
        resp["events"] = [DaemonEvent.from_dict(e) if isinstance(e, dict) else e for e in raw_events]
        return resp

    async def detach(self) -> Dict[str, Any]:
        resp = await self._send_request({"action": "detach"})
        self._event_callback = None
        return resp

    async def send_input(self, session_id: str, text: str, workspace: Optional[str] = None) -> bool:
        resp = await self._send_request({
            "action": "send_input",
            "session_id": session_id,
            "text": text,
            "workspace": workspace,
        })
        return resp.get("status") == "ok"

    async def stop_daemon(self) -> Dict[str, Any]:
        return await self._send_request({"action": "stop"})

    async def close(self) -> None:
        self._connected = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
