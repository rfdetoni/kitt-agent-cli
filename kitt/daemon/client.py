from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from kitt.daemon.protocol import DaemonEvent, decode_line, encode_message
from kitt.daemon.server import get_default_socket_path, get_default_token_path


class DaemonClient:
    """Client for attaching to and interacting with a running KITT Daemon."""

    def __init__(
        self,
        socket_path: Optional[Path] = None,
        token_path: Optional[Path] = None,
    ):
        self.socket_path = socket_path or get_default_socket_path()
        self.token_path = token_path or get_default_token_path()
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._listener_task: Optional[asyncio.Task] = None
        self._event_callback: Optional[Callable[[DaemonEvent], None]] = None

    async def connect(self) -> bool:
        """Connect to daemon and authenticate."""
        if not self.socket_path.exists():
            return False
        if not self.token_path.exists():
            return False

        token = self.token_path.read_text(encoding="utf-8").strip()

        try:
            if sys.platform != "win32":
                self.reader, self.writer = await asyncio.open_unix_connection(str(self.socket_path))
            else:
                self.reader, self.writer = await asyncio.open_connection("127.0.0.1", 0)

            # Send auth request
            self.writer.write(encode_message({"action": "auth", "token": token}))
            await self.writer.drain()

            line = await self.reader.readline()
            resp = decode_line(line)
            if resp.get("status") == "ok":
                self._connected = True
                return True
            return False
        except Exception:
            return False

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

    async def _send_request(self, req: Dict[str, Any]) -> Dict[str, Any]:
        if not self.writer or not self.reader:
            raise ConnectionError("Not connected to daemon")
        self.writer.write(encode_message(req))
        await self.writer.drain()
        line = await self.reader.readline()
        return decode_line(line)

    async def list_sessions(self, workspace: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_request({"action": "list_sessions", "workspace": workspace})

    async def attach(
        self,
        session_id: str,
        last_sequence: int = 0,
        event_callback: Optional[Callable[[DaemonEvent], None]] = None,
        workspace: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._event_callback = event_callback
        resp = await self._send_request({
            "action": "attach",
            "session_id": session_id,
            "last_sequence": last_sequence,
            "workspace": workspace,
        })
        if resp.get("status") == "ok" and event_callback:
            if not self._listener_task or self._listener_task.done():
                self._listener_task = asyncio.create_task(self._listen_events())
        return resp

    async def detach(self) -> Dict[str, Any]:
        resp = await self._send_request({"action": "detach"})
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
        return resp

    async def send_input(self, session_id: str, text: str, workspace: Optional[str] = None) -> Dict[str, Any]:
        return await self._send_request({
            "action": "send_input",
            "session_id": session_id,
            "text": text,
            "workspace": workspace,
        })

    async def stop_daemon(self) -> Dict[str, Any]:
        return await self._send_request({"action": "stop"})

    async def _listen_events(self) -> None:
        try:
            while self._connected and self.reader:
                line = await self.reader.readline()
                if not line:
                    break
                msg = decode_line(line)
                if msg.get("type") == "EVENT" and "event" in msg:
                    evt = DaemonEvent.from_dict(msg["event"])
                    if self._event_callback:
                        self._event_callback(evt)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def close(self) -> None:
        self._connected = False
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
