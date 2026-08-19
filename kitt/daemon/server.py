from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import stat
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from kitt.core.runtime import KittRuntime
from kitt.daemon.protocol import DaemonEvent, decode_line, encode_message
from kitt.history.database import HistoryDatabase


def get_default_socket_path() -> Path:
    if sys.platform != "win32":
        run_dir = Path(os.getenv("XDG_RUNTIME_DIR", "/tmp")) / "kitt"
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(run_dir, 0o700)
        except Exception:
            pass
        return run_dir / "daemon.sock"
    else:
        return Path.home() / ".kitt" / "daemon.sock"


def get_default_token_path() -> Path:
    token_dir = Path.home() / ".kitt"
    token_dir.mkdir(parents=True, exist_ok=True)
    return token_dir / "daemon.token"


class DaemonServer:
    """Persistent local daemon running KITT sessions independently of TUI attachments."""

    def __init__(
        self,
        socket_path: Optional[Path] = None,
        token_path: Optional[Path] = None,
        workspace_root: Optional[str] = None,
    ):
        self.socket_path = socket_path or get_default_socket_path()
        self.token_path = token_path or get_default_token_path()
        self.workspace_root = Path(workspace_root or os.getcwd()).resolve()
        self.token = ""
        self._server: Optional[asyncio.Server] = None
        self._running = False
        self._subscribers: Dict[str, Set[asyncio.StreamWriter]] = {}
        self._runtimes: Dict[str, KittRuntime] = {}
        self._lock = asyncio.Lock()

    def _ensure_token(self) -> str:
        if self.token_path.exists():
            try:
                content = self.token_path.read_text(encoding="utf-8").strip()
                if content:
                    self.token = content
                    return self.token
            except Exception:
                pass
        self.token = secrets.token_hex(32)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        mode = stat.S_IRUSR | stat.S_IWUSR  # 0600
        fd = os.open(str(self.token_path), flags, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(self.token)
        try:
            os.chmod(self.token_path, 0o600)
        except Exception:
            pass
        return self.token

    def _get_or_create_runtime(self, workspace_path: Optional[str] = None) -> KittRuntime:
        root = str(Path(workspace_path or self.workspace_root).resolve())
        if root not in self._runtimes:
            rt = KittRuntime.build(root)
            self._runtimes[root] = rt
        return self._runtimes[root]

    async def start(self) -> None:
        """Start the IPC daemon server."""
        self._ensure_token()
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except Exception:
                pass

        if sys.platform != "win32":
            self._server = await asyncio.start_unix_server(
                self._handle_client,
                path=str(self.socket_path),
            )
            try:
                os.chmod(self.socket_path, 0o600)
            except Exception:
                pass
        else:
            self._server = await asyncio.start_server(
                self._handle_client,
                host="127.0.0.1",
                port=0,
            )

        self._running = True

    async def stop(self) -> None:
        """Gracefully stop the daemon."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except Exception:
                pass
        for rt in self._runtimes.values():
            try:
                rt.close()
            except Exception:
                pass
        self._runtimes.clear()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        authenticated = False
        attached_session: Optional[str] = None

        try:
            while self._running:
                line = await reader.readline()
                if not line:
                    break

                try:
                    msg = decode_line(line)
                except Exception as exc:
                    writer.write(encode_message({"status": "error", "error": f"Invalid JSON: {exc}"}))
                    await writer.drain()
                    continue

                action = msg.get("action", "")

                # 1. Authentication
                if not authenticated:
                    if action == "auth":
                        client_token = msg.get("token", "")
                        if secrets.compare_digest(client_token, self.token):
                            authenticated = True
                            writer.write(encode_message({"status": "ok", "action": "auth"}))
                        else:
                            writer.write(encode_message({"status": "error", "error": "Unauthorized"}))
                            await writer.drain()
                            break
                    else:
                        writer.write(encode_message({"status": "error", "error": "Authentication required"}))
                        await writer.drain()
                        break
                    await writer.drain()
                    continue

                # 2. Authenticated Actions
                if action == "ping":
                    writer.write(encode_message({"status": "ok", "action": "ping", "time": time.time()}))
                elif action == "list_sessions":
                    rt = self._get_or_create_runtime(msg.get("workspace"))
                    convs = rt.history.list_history(limit=50)
                    active_conv = rt.history.get_active_read_only()
                    active_id = active_conv["id"] if active_conv else ""
                    writer.write(encode_message({
                        "status": "ok",
                        "action": "list_sessions",
                        "active_session_id": active_id,
                        "sessions": [
                            {
                                "id": c.get("id", ""),
                                "title": c.get("title", ""),
                                "status": c.get("status", ""),
                                "updated_at": c.get("updated_at", 0),
                                "created_at": c.get("created_at", 0),
                            }
                            for c in convs
                        ],
                    }))
                elif action == "attach":
                    session_id = msg.get("session_id", "")
                    last_seq = msg.get("last_sequence", 0)
                    attached_session = session_id

                    if session_id not in self._subscribers:
                        self._subscribers[session_id] = set()
                    self._subscribers[session_id].add(writer)

                    # Replay past events since last_seq
                    rt = self._get_or_create_runtime(msg.get("workspace"))
                    past_events = self._get_events_since(rt.database, session_id, last_seq)

                    writer.write(encode_message({
                        "status": "ok",
                        "action": "attach",
                        "session_id": session_id,
                        "events": [e.to_dict() for e in past_events],
                    }))
                elif action == "detach":
                    if attached_session and attached_session in self._subscribers:
                        self._subscribers[attached_session].discard(writer)
                    attached_session = None
                    writer.write(encode_message({"status": "ok", "action": "detach"}))
                elif action == "send_input":
                    session_id = msg.get("session_id", "")
                    text = msg.get("text", "")
                    rt = self._get_or_create_runtime(msg.get("workspace"))
                    # Record event and submit turn in background
                    asyncio.create_task(self._execute_turn(rt, session_id, text))
                    writer.write(encode_message({"status": "ok", "action": "send_input", "session_id": session_id}))
                elif action == "stop":
                    writer.write(encode_message({"status": "ok", "action": "stop"}))
                    await writer.drain()
                    asyncio.create_task(self.stop())
                    break
                else:
                    writer.write(encode_message({"status": "error", "error": f"Unknown action '{action}'"}))

                await writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            if attached_session and attached_session in self._subscribers:
                self._subscribers[attached_session].discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    def _get_events_since(self, db: HistoryDatabase, session_id: str, last_seq: int) -> List[DaemonEvent]:
        with db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, event_type, payload_json, created_at
                FROM daemon_events
                WHERE session_id = ? AND id > ?
                ORDER BY id ASC LIMIT 200
                """,
                (session_id, last_seq),
            ).fetchall()
            events = []
            for r in rows:
                try:
                    payload = json.loads(r[3])
                except Exception:
                    payload = {}
                events.append(DaemonEvent(
                    sequence_id=r[0],
                    session_id=r[1],
                    event_type=r[2],
                    payload=payload,
                    created_at=r[4],
                ))
            return events

    def record_event(self, db: HistoryDatabase, session_id: str, event_type: str, payload: Dict[str, Any]) -> DaemonEvent:
        now = time.time()
        payload_json = json.dumps(payload, ensure_ascii=False)
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO daemon_events (session_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, event_type, payload_json, now),
            )
            seq_id = cur.lastrowid
            conn.commit()

        evt = DaemonEvent(
            sequence_id=seq_id,
            session_id=session_id,
            event_type=event_type,
            payload=payload,
            created_at=now,
        )
        self._broadcast_event(evt)
        return evt

    def _broadcast_event(self, event: DaemonEvent) -> None:
        subscribers = self._subscribers.get(event.session_id, set())
        dead = set()
        msg_bytes = encode_message({"type": "EVENT", "event": event.to_dict()})

        for writer in list(subscribers):
            try:
                writer.write(msg_bytes)
            except Exception:
                dead.add(writer)

        for d in dead:
            subscribers.discard(d)

    async def _execute_turn(self, rt: KittRuntime, session_id: str, text: str) -> None:
        self.record_event(rt.database, session_id, "TurnStarted", {"text": text[:100]})
        try:
            req = rt.processor.create_request(text, mode="auto")
            res = await rt.processor.run_turn(req)
            self.record_event(
                rt.database,
                session_id,
                "TurnCompleted",
                {
                    "status": "COMPLETED",
                    "success": getattr(res, "success", True),
                    "output": str(getattr(res, "text", "") or "")[:200],
                },
            )
        except Exception as exc:
            self.record_event(
                rt.database,
                session_id,
                "TurnFailed",
                {"error": str(exc)},
            )
