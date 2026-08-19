from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import stat
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from kitt.core.runtime import KittRuntime
from kitt.daemon.protocol import DaemonEvent, decode_line, encode_message
from kitt.daemon.transport import IPCTransport
from kitt.history.database import HistoryDatabase

logger = logging.getLogger("kitt.daemon.server")


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
        self.transport = IPCTransport(self.workspace_root)
        self.token = ""
        self._server: Optional[asyncio.Server] = None
        self._running = False
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}
        self._client_queues: Dict[asyncio.StreamWriter, asyncio.Queue] = {}
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._runtimes: Dict[str, KittRuntime] = {}
        self._lock = asyncio.Lock()

    def _ensure_token(self) -> str:
        """Read and validate existing token with strict permissions, or generate new 0600 token."""
        if self.token_path.exists():
            st = self.token_path.stat()
            # Security check: mode must not be world or group readable
            if sys.platform != "win32":
                if (st.st_mode & 0o077) != 0:
                    raise PermissionError(f"Insecure token file permissions ({oct(st.st_mode)}). Mode 0600 required.")
                if hasattr(os, "getuid") and st.st_uid != os.getuid():
                    raise PermissionError("Token file is owned by a different user.")
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
            if hasattr(rt, "goal_scheduler") and rt.goal_scheduler:
                rt.goal_scheduler.start(interval_seconds=1.0)
        return self._runtimes[root]

    async def start(self) -> None:
        """Start the IPC daemon server with platform transport."""
        self._ensure_token()
        transport_type, address, port = self.transport.get_server_endpoint()

        if transport_type == "unix":
            if self.socket_path.exists():
                try:
                    self.socket_path.unlink()
                except Exception:
                    pass
            self._server = await asyncio.start_unix_server(
                self._handle_client,
                path=str(self.socket_path),
            )
            try:
                os.chmod(self.socket_path, 0o600)
            except Exception:
                pass
            self.transport.write_endpoint_metadata("unix", str(self.socket_path), None)
        else:
            self._server = await asyncio.start_server(
                self._handle_client,
                host=address,
                port=port or 0,
            )
            sockets = self._server.sockets
            actual_port = sockets[0].getsockname()[1] if sockets else 0
            self.transport.write_endpoint_metadata("tcp", address, actual_port)

        self.transport.write_pid(os.getpid())
        self._running = True

        # Pre-initialize workspace runtime and scheduler
        self._get_or_create_runtime(str(self.workspace_root))

    async def stop(self) -> None:
        """Gracefully stop the daemon and schedulers."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        self.transport.cleanup()

        for rt in self._runtimes.values():
            try:
                if hasattr(rt, "goal_scheduler") and rt.goal_scheduler:
                    rt.goal_scheduler.stop()
                rt.close()
            except Exception:
                pass
        self._runtimes.clear()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        authenticated = False
        attached_session: Optional[str] = None
        client_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._client_queues[writer] = client_queue

        writer_task = asyncio.create_task(self._client_writer_loop(writer, client_queue))

        try:
            while self._running:
                line = await reader.readline()
                if not line:
                    break

                try:
                    msg = decode_line(line)
                except Exception as exc:
                    await client_queue.put(encode_message({"type": "RESPONSE", "status": "error", "error": f"Invalid JSON: {exc}"}))
                    continue

                req_id = msg.get("request_id", "")
                action = msg.get("action", "")

                # 1. Authentication
                if not authenticated:
                    if action == "auth":
                        client_token = msg.get("token", "")
                        if secrets.compare_digest(client_token, self.token):
                            authenticated = True
                            await client_queue.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "ok", "action": "auth"}))
                        else:
                            await client_queue.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "error", "error": "Unauthorized"}))
                            break
                    else:
                        await client_queue.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "error", "error": "Authentication required"}))
                        break
                    continue

                # 2. Authenticated Actions
                if action == "ping":
                    await client_queue.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "ok", "action": "ping", "time": time.time()}))
                elif action == "list_sessions":
                    rt = self._get_or_create_runtime(msg.get("workspace"))
                    convs = rt.history.list_history(limit=50)
                    active_conv = rt.history.get_active_read_only()
                    active_id = active_conv["id"] if active_conv else ""
                    await client_queue.put(encode_message({
                        "type": "RESPONSE",
                        "request_id": req_id,
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
                    rt = self._get_or_create_runtime(msg.get("workspace"))

                    # Validate session existence and workspace scoping
                    conv = rt.history.repo.get_conversation(session_id)
                    if conv and conv.get("workspace_id") and conv["workspace_id"] != rt.workspace_id:
                        await client_queue.put(encode_message({
                            "type": "RESPONSE",
                            "request_id": req_id,
                            "status": "error",
                            "error": f"Session '{session_id}' does not belong to workspace",
                        }))
                        continue

                    if not conv:
                        try:
                            rt.history.repo.create_conversation(id=session_id, workspace_id=rt.workspace_id, title=f"Session {session_id[:8]}")
                        except Exception:
                            pass

                    attached_session = session_id
                    if session_id not in self._subscribers:
                        self._subscribers[session_id] = set()
                    self._subscribers[session_id].add(client_queue)

                    # Replay past events with pagination
                    past_events, has_more, next_seq = self._get_events_since(rt.database, session_id, last_seq)

                    await client_queue.put(encode_message({
                        "type": "RESPONSE",
                        "request_id": req_id,
                        "status": "ok",
                        "action": "attach",
                        "session_id": session_id,
                        "events": [e.to_dict() for e in past_events],
                        "has_more": has_more,
                        "next_sequence": next_seq,
                    }))
                elif action == "detach":
                    if attached_session and attached_session in self._subscribers:
                        self._subscribers[attached_session].discard(client_queue)
                    attached_session = None
                    await client_queue.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "ok", "action": "detach"}))
                elif action == "send_input":
                    session_id = msg.get("session_id", "")
                    text = msg.get("text", "")
                    rt = self._get_or_create_runtime(msg.get("workspace"))

                    # Validate session
                    conv = rt.history.repo.get_conversation(session_id)
                    if conv and conv.get("workspace_id") and conv["workspace_id"] != rt.workspace_id:
                        await client_queue.put(encode_message({
                            "type": "RESPONSE",
                            "request_id": req_id,
                            "status": "error",
                            "error": f"Session '{session_id}' does not belong to workspace",
                        }))
                        continue

                    if not conv:
                        try:
                            rt.history.repo.create_conversation(id=session_id, workspace_id=rt.workspace_id, title=f"Session {session_id[:8]}")
                        except Exception:
                            pass

                    # Record event and submit turn in session-isolated task
                    asyncio.create_task(self._execute_turn(rt, session_id, text))
                    await client_queue.put(encode_message({
                        "type": "RESPONSE",
                        "request_id": req_id,
                        "status": "ok",
                        "action": "send_input",
                        "session_id": session_id,
                    }))
                elif action == "stop":
                    await client_queue.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "ok", "action": "stop"}))
                    asyncio.create_task(self.stop())
                    break
                else:
                    await client_queue.put(encode_message({
                        "type": "RESPONSE",
                        "request_id": req_id,
                        "status": "error",
                        "error": f"Unknown action '{action}'",
                    }))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"Daemon client connection error: {exc}")
        finally:
            if attached_session and attached_session in self._subscribers:
                self._subscribers[attached_session].discard(client_queue)
            self._client_queues.pop(writer, None)
            writer_task.cancel()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _client_writer_loop(self, writer: asyncio.StreamWriter, q: asyncio.Queue) -> None:
        """Drains outbound event queue with backpressure protection."""
        try:
            while self._running:
                data = await q.get()
                writer.write(data)
                await writer.drain()
                q.task_done()
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass

    def _get_events_since(
        self, db: HistoryDatabase, session_id: str, last_seq: int, limit: int = 200
    ) -> Tuple[List[DaemonEvent], bool, int]:
        with db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, event_type, payload_json, created_at
                FROM daemon_events
                WHERE session_id = ? AND id > ?
                ORDER BY id ASC LIMIT ?
                """,
                (session_id, last_seq, limit + 1),
            ).fetchall()
            has_more = len(rows) > limit
            trimmed_rows = rows[:limit]
            events = []
            next_seq = last_seq
            for r in trimmed_rows:
                try:
                    payload = json.loads(r[3])
                except Exception:
                    payload = {}
                next_seq = r[0]
                events.append(DaemonEvent(
                    sequence_id=r[0],
                    session_id=r[1],
                    event_type=r[2],
                    payload=payload,
                    created_at=r[4],
                ))
            return events, has_more, next_seq

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
        msg_bytes = encode_message({"type": "EVENT", "session_id": event.session_id, "event": event.to_dict()})

        for q in list(subscribers):
            try:
                q.put_nowait(msg_bytes)
            except asyncio.QueueFull:
                logger.warning(f"Subscriber queue full for session {event.session_id}; dropping event {event.sequence_id}")

    async def _execute_turn(self, rt: KittRuntime, session_id: str, text: str) -> None:
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
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
