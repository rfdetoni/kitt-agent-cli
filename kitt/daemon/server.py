from __future__ import annotations

import asyncio
import dataclasses
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
from kitt.core.turn_command import TurnCommand
from kitt.daemon.protocol import DaemonEvent, decode_line, encode_message
from kitt.daemon.transport import IPCTransport
from kitt.history.database import HistoryDatabase
from kitt.tools.approval import ApprovalGrant

logger = logging.getLogger("kitt.daemon.server")


def get_default_socket_path() -> Path:
    if sys.platform != "win32":
        run_dir = Path(os.getenv("XDG_RUNTIME_DIR", "/tmp")) / "kitt"
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(run_dir, 0o700)
        except OSError:
            pass
        return run_dir / "daemon.sock"
    return Path.home() / ".kitt" / "daemon.sock"


def get_default_token_path() -> Path:
    p = Path.home() / ".kitt"
    p.mkdir(parents=True, exist_ok=True)
    return p / "daemon.token"


def _jsonable(value):
    if dataclasses.is_dataclass(value):
        return {k: _jsonable(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class DaemonServer:
    def __init__(self, workspace_root=None, socket_path=None, token_path=None,
                 context_client=None, execution_client=None):
        self.workspace_root = Path(workspace_root or os.getcwd()).resolve()
        self.transport = IPCTransport(self.workspace_root)
        self.socket_path = Path(socket_path) if socket_path else self.transport.socket_path
        self.token_path = Path(token_path) if token_path else self.transport.token_file
        self.context_client = context_client
        self.execution_client = execution_client
        self.token = ""
        self._server = None
        self._running = False
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}
        self._client_queues = {}
        self._session_locks = {}
        self._workspace_locks = {}
        self._runtimes = {}
        self._instance_lock_fd = None

    def _ensure_token(self) -> str:
        try:
            existing = self.transport.read_secret(self.token_path)
            if existing:
                self.token = existing
                return existing
        except PermissionError:
            # Unsafe existing token is never reused.
            self.token_path.unlink(missing_ok=True)
        self.token = secrets.token_hex(32)
        self.transport.secure_write(self.token_path, self.token, exclusive=True)
        return self.token

    async def _get_or_create_runtime(self, workspace_path=None):
        root = str(Path(workspace_path or self.workspace_root).resolve())
        if root not in self._runtimes:
            rt = KittRuntime.build(root)
            if self.context_client is not None:
                rt.processor.context_client = self.context_client
            if self.execution_client is not None:
                rt.processor.execution_client = self.execution_client
            self._runtimes[root] = rt
        rt = self._runtimes[root]
        await rt.start()
        return rt

    async def start(self):
        self._instance_lock_fd = self.transport.acquire_instance_lock()
        try:
            self._ensure_token()
            transport_type, address, port = self.transport.get_server_endpoint()
            if transport_type == "unix":
                # Lock ownership is established before removing stale endpoint.
                self.socket_path.unlink(missing_ok=True)
                self._server = await asyncio.start_unix_server(
                    self._handle_client, path=str(self.socket_path)
                )
                os.chmod(self.socket_path, 0o600)
                self.transport.write_endpoint_metadata("unix", str(self.socket_path), None)
            else:
                self._server = await asyncio.start_server(
                    self._handle_client, host=address, port=port or 0
                )
                actual = self._server.sockets[0].getsockname()[1]
                self.transport.write_endpoint_metadata("tcp", address, actual)
            self.transport.write_pid(os.getpid())
            self._running = True
            await self._get_or_create_runtime(str(self.workspace_root))
        except Exception:
            self._running = False
            if self._server is not None:
                self._server.close()
                try:
                    await self._server.wait_closed()
                except Exception:
                    logger.debug(
                        "Daemon server close after startup failure failed",
                        exc_info=True,
                    )
                self._server = None
            for runtime in list(self._runtimes.values()):
                try:
                    await runtime.aclose()
                except Exception:
                    logger.debug(
                        "Runtime rollback after daemon startup failure failed",
                        exc_info=True,
                    )
            self._runtimes.clear()
            try:
                self.transport.cleanup()
            except Exception:
                logger.debug(
                    "Daemon transport cleanup after startup failure failed",
                    exc_info=True,
                )
            self.transport.release_instance_lock(self._instance_lock_fd)
            self._instance_lock_fd = None
            raise

    async def stop(self):
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in list(self._client_queues):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        for rt in list(self._runtimes.values()):
            try:
                await rt.aclose()
            except Exception:
                logger.exception("Runtime shutdown failure")
        self._runtimes.clear()
        self.transport.cleanup()
        self.transport.release_instance_lock(self._instance_lock_fd)
        self._instance_lock_fd = None

    def _workspace_lock(self, workspace_root: str) -> asyncio.Lock:
        return self._workspace_locks.setdefault(workspace_root, asyncio.Lock())

    def _workspace_allowed(self, workspace: Any) -> bool:
        if workspace in {None, ""}:
            return True
        try:
            return Path(workspace).resolve() == self.workspace_root
        except Exception:
            return False

    async def _plugin_action(self, rt, action: str, plugin_id: str) -> dict[str, Any]:
        ext = rt.extensions
        if ext is None:
            raise RuntimeError("Extensions manager is not available")
        plugin_key = str(plugin_id or "").strip().lower()
        if not plugin_key:
            raise ValueError("Plugin name is required")
        manifests = ext.plugins.discover()
        manifest = manifests.get(plugin_key)
        if action != "plugin.enable" and manifest is None:
            raise ValueError(f"Plugin '{plugin_key}' not found")
        if action == "plugin.enable":
            ext.plugins.enable(plugin_key)
            if ext.state == ext.STATE_STARTED:
                try:
                    await ext.plugins.start(plugin_key)
                except Exception:
                    ext.plugins.disable(plugin_key)
                    raise
            manifest = ext.plugins.discover().get(plugin_key)
        elif action == "plugin.disable":
            await ext.plugins.unload(plugin_key)
            ext.plugins.disable(plugin_key)
        elif action == "plugin.reload":
            await ext.plugins.reload(plugin_key)
        elif action == "plugin.unload":
            await ext.plugins.unload(plugin_key)
        else:
            raise ValueError(f"Unknown plugin action '{action}'")
        instance = ext.plugins.get(plugin_key)
        return {
            "plugin": plugin_key,
            "enabled": bool(manifest and ext.plugins.is_enabled(plugin_key, manifest)),
            "loaded": instance is not None,
            "state": instance.state.value if instance is not None else "UNLOADED",
            "trusted": bool(manifest and ext.plugin_trust.is_trusted(manifest)),
        }

    async def _ensure_mcp_clients(self, rt, server_id: Optional[str] = None):
        ext = rt.extensions
        if ext is None:
            raise RuntimeError("Extensions manager is not available")
        if server_id:
            return [await ext.mcp.connect(server_id)]
        clients = []
        for cfg in ext.mcp.list_servers():
            if cfg.enabled and (cfg.command or cfg.url):
                clients.append(await ext.mcp.connect(cfg.server_id))
        return clients

    def _mcp_status_payload(self, rt, server_id: Optional[str] = None):
        ext = rt.extensions
        if ext is None:
            raise RuntimeError("Extensions manager is not available")
        configs = ext.mcp.list_servers()
        if server_id:
            configs = [cfg for cfg in configs if cfg.server_id == server_id]
        return [
            {
                "server_id": cfg.server_id,
                "transport": cfg.transport,
                "enabled": cfg.enabled,
                "trust": cfg.trust,
                "state": ext.mcp.get_server_status(cfg.server_id).value,
                "allow_tools": list(cfg.allow_tools or []),
                "deny_tools": list(cfg.deny_tools or []),
                "timeout_seconds": cfg.timeout_seconds,
            }
            for cfg in configs
        ]

    def _extensions_status_payload(self, rt) -> dict[str, Any]:
        ext = rt.extensions
        if ext is None:
            raise RuntimeError("Extensions manager is not available")
        manifests = ext.plugins.discover()
        plugins = []
        for plugin_id, manifest in manifests.items():
            instance = ext.plugins.get(plugin_id)
            plugins.append(
                {
                    "name": manifest.name,
                    "version": manifest.version,
                    "enabled": ext.plugins.is_enabled(plugin_id, manifest),
                    "trusted": ext.plugin_trust.is_trusted(manifest),
                    "loaded": instance is not None,
                    "state": instance.state.value if instance is not None else "UNLOADED",
                    "critical": bool(manifest.is_critical),
                }
            )
        return {
            "workspace_root": str(self.workspace_root),
            "state": ext.state,
            "plugins": plugins,
            "mcp": self._mcp_status_payload(rt),
        }

    async def _mcp_action(self, rt, action: str, server_id: Optional[str]) -> dict[str, Any]:
        ext = rt.extensions
        if ext is None:
            raise RuntimeError("Extensions manager is not available")
        if action == "mcp.connect":
            if not server_id:
                raise ValueError("MCP server name is required")
            client = await ext.mcp.connect(server_id)
            return {
                "server_id": server_id,
                "state": ext.mcp.get_server_status(server_id).value,
                "tools": len(await client.list_tools()),
            }
        if action == "mcp.disconnect":
            if not server_id:
                raise ValueError("MCP server name is required")
            await ext.mcp.disconnect(server_id)
            return {"server_id": server_id, "state": ext.mcp.get_server_status(server_id).value}
        if action == "mcp.status":
            return {"servers": self._mcp_status_payload(rt, server_id)}
        if action == "mcp.tools":
            await self._ensure_mcp_clients(rt, server_id)
            tools = ext.mcp.list_tools(server_id)
            return {
                "tools": [
                    {
                        "server_id": tool.server_id,
                        "name": tool.name,
                        "full_name": tool.full_name,
                        "description": tool.description,
                    }
                    for tool in tools
                ]
            }
        if action == "mcp.resources":
            clients = await self._ensure_mcp_clients(rt, server_id)
            resources = []
            for client in clients:
                for resource in await client.list_resources():
                    resources.append(
                        {
                            "server_id": resource.server_id,
                            "name": resource.name,
                            "uri": resource.uri,
                            "mime_type": resource.mime_type,
                            "description": resource.description,
                        }
                    )
            return {"resources": resources}
        raise ValueError(f"Unknown MCP action '{action}'")

    async def _handle_client(self, reader, writer):
        authenticated = False
        attached_session = None
        q = asyncio.Queue(maxsize=500)
        self._client_queues[writer] = q
        writer_task = asyncio.create_task(self._client_writer_loop(writer, q))
        try:
            while self._running:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = decode_line(line)
                except Exception as exc:
                    await q.put(encode_message({"type": "RESPONSE", "status": "error", "error": f"Invalid JSON: {exc}"}))
                    continue
                req_id = msg.get("request_id", "")
                action = msg.get("action", "")

                if not authenticated:
                    if action != "auth":
                        await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "error", "error": "Authentication required"}))
                        break
                    if not secrets.compare_digest(str(msg.get("token", "")), self.token):
                        await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "error", "error": "Unauthorized"}))
                        break
                    authenticated = True
                    await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "ok", "action": "auth"}))
                    continue

                if action == "ping":
                    await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "ok", "action": "ping"}))
                    continue

                if (
                    action in {
                        "extensions.status",
                        "plugin.enable",
                        "plugin.disable",
                        "plugin.reload",
                        "plugin.unload",
                        "mcp.connect",
                        "mcp.disconnect",
                        "mcp.status",
                        "mcp.tools",
                        "mcp.resources",
                    }
                    and not self._workspace_allowed(msg.get("workspace"))
                ):
                    await q.put(encode_message({
                        "type": "RESPONSE",
                        "request_id": req_id,
                        "status": "error",
                        "error": "Cross-workspace extension control blocked",
                    }))
                    continue

                rt = await self._get_or_create_runtime(msg.get("workspace"))

                if action == "list_sessions":
                    convs = rt.history.list_history(limit=50)
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id, "status": "ok",
                        "sessions": [{"id": c.get("id"), "title": c.get("title"), "status": c.get("status")} for c in convs],
                    }))
                elif action == "create_session":
                    conv = rt.history.new_conversation(msg.get("title", "New Session"))
                    await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "ok", "session_id": conv["id"]}))
                elif action == "attach":
                    sid = str(msg.get("session_id", ""))
                    conv = rt.history.repo.get_conversation(sid)
                    if not conv:
                        await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "error", "error": f"Unknown session '{sid}'"}))
                        continue
                    if conv.get("workspace_id") != rt.workspace_id:
                        await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "error", "error": "Cross-workspace session attach blocked"}))
                        continue
                    if attached_session and attached_session in self._subscribers:
                        self._subscribers[attached_session].discard(q)
                    attached_session = sid
                    self._subscribers.setdefault(sid, set()).add(q)
                    events, more, next_seq = self._get_events_since(
                        rt.database, sid, int(msg.get("last_sequence", 0))
                    )
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id, "status": "ok",
                        "action": "attach", "session_id": sid,
                        "events": [e.to_dict() for e in events],
                        "has_more": more, "next_sequence": next_seq,
                    }))
                elif action == "detach":
                    if attached_session in self._subscribers:
                        self._subscribers[attached_session].discard(q)
                    attached_session = None
                    await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "ok"}))
                elif action == "send_input":
                    sid = str(msg.get("session_id", ""))
                    conv = rt.history.repo.get_conversation(sid)
                    if not conv:
                        await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "error", "error": f"Unknown session '{sid}'"}))
                        continue
                    if conv.get("workspace_id") != rt.workspace_id:
                        await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "error", "error": "Cross-workspace turn blocked"}))
                        continue
                    cmd = TurnCommand(
                        conversation_id=sid,
                        prompt=str(msg.get("text", "")),
                        mode=str(msg.get("mode", "auto")),
                        explicit_files=set(msg.get("explicit_files") or ()),
                        no_history=bool(msg.get("no_history", False)),
                    )
                    asyncio.create_task(self._execute_turn(rt, cmd))
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id, "status": "ok",
                        "action": "send_input", "session_id": sid, "turn_id": cmd.turn_id,
                    }))
                elif action == "continue_turn":
                    sid = str(msg.get("session_id", ""))
                    conv = rt.history.repo.get_conversation(sid)
                    if not conv or conv.get("workspace_id") != rt.workspace_id:
                        await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "error", "error": "Invalid session"}))
                        continue
                    try:
                        g = msg["grant"]
                        grant = ApprovalGrant(
                            approval_id=g["approval_id"], turn_id=g["turn_id"],
                            conversation_id=g["conversation_id"], workspace_id=g["workspace_id"],
                            action_hash=g["action_hash"], granted_at=float(g["granted_at"]),
                            expires_at=float(g["expires_at"]), nonce=g["nonce"],
                        )
                    except Exception as exc:
                        await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "error", "error": f"Invalid grant: {exc}"}))
                        continue
                    asyncio.create_task(self._continue_turn(rt, sid, grant))
                    await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "ok"}))
                elif action == "cancel_turn":
                    sid = str(msg.get("session_id", ""))
                    turn_id = str(msg.get("turn_id", ""))
                    for event in rt.processor.cancel_turn(turn_id, "Cancelled via daemon IPC"):
                        self._record_turn_event(rt.database, sid, event)
                    await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "ok"}))
                elif action == "stop":
                    await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "ok"}))
                    asyncio.create_task(self.stop())
                    break
                elif action in {
                    "extensions.status",
                    "plugin.enable",
                    "plugin.disable",
                    "plugin.reload",
                    "plugin.unload",
                    "mcp.connect",
                    "mcp.disconnect",
                    "mcp.status",
                    "mcp.tools",
                    "mcp.resources",
                }:
                    lock = self._workspace_lock(str(self.workspace_root))
                    try:
                        async with lock:
                            if action == "extensions.status":
                                payload = self._extensions_status_payload(rt)
                            elif action.startswith("plugin."):
                                payload = await self._plugin_action(
                                    rt, action, str(msg.get("name", ""))
                                )
                            else:
                                payload = await self._mcp_action(
                                    rt,
                                    action,
                                    (
                                        str(msg.get("server_id", "")).strip().lower()
                                        or None
                                    ),
                                )
                    except Exception as exc:
                        await q.put(encode_message({
                            "type": "RESPONSE",
                            "request_id": req_id,
                            "status": "error",
                            "error": str(exc),
                        }))
                        continue
                    await q.put(encode_message({
                        "type": "RESPONSE",
                        "request_id": req_id,
                        "status": "ok",
                        "action": action,
                        **payload,
                    }))
                else:
                    await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "error", "error": f"Unknown action '{action}'"}))
        finally:
            if attached_session in self._subscribers:
                self._subscribers[attached_session].discard(q)
            self._client_queues.pop(writer, None)
            writer_task.cancel()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _client_writer_loop(self, writer, q):
        try:
            while self._running:
                data = await q.get()
                writer.write(data)
                await writer.drain()
                q.task_done()
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass

    def _get_events_since(self, db, session_id, last_seq, limit=200):
        with db.get_connection() as conn:
            rows = conn.execute(
                """SELECT id,session_id,event_type,payload_json,created_at
                   FROM daemon_events WHERE session_id=? AND id>?
                   ORDER BY id ASC LIMIT ?""",
                (session_id, last_seq, limit + 1),
            ).fetchall()
        more = len(rows) > limit
        events, next_seq = [], last_seq
        for r in rows[:limit]:
            evt = DaemonEvent(
                sequence_id=r["id"], session_id=r["session_id"],
                event_type=r["event_type"],
                payload=json.loads(r["payload_json"] or "{}"),
                created_at=r["created_at"],
            )
            events.append(evt)
            next_seq = max(next_seq, r["id"])
        return events, more, next_seq

    def record_event(self, db, session_id, event_type, payload):
        now = time.time()
        payload = _jsonable(payload)
        with db.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO daemon_events(session_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (session_id, event_type, json.dumps(payload, ensure_ascii=False), now),
            )
            seq = cur.lastrowid
        evt = DaemonEvent(seq, session_id, event_type, payload, now)
        self._broadcast_event(evt)
        return evt

    def _record_turn_event(self, db, session_id, event):
        payload = dataclasses.asdict(event) if dataclasses.is_dataclass(event) else getattr(event, "__dict__", {})
        return self.record_event(db, session_id, type(event).__name__, payload)

    def _broadcast_event(self, event):
        msg = encode_message({"type": "EVENT", "session_id": event.session_id, "event": event.to_dict()})
        for q in list(self._subscribers.get(event.session_id, set())):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # Never drop persisted events silently and never evict request
                # responses from the same queue. Disconnect the slow client;
                # its next attach replays all missing events by sequence id.
                for writer, client_q in list(self._client_queues.items()):
                    if client_q is q:
                        try:
                            writer.close()
                        except Exception:
                            pass
                        break
                logger.warning(
                    "Disconnected slow client for session %s at sequence %s; replay required",
                    event.session_id, event.sequence_id,
                )

    async def _execute_turn(self, rt, cmd):
        lock = self._session_locks.setdefault(cmd.conversation_id, asyncio.Lock())
        async with lock:
            if not cmd.no_history:
                try:
                    rt.history.repo.save_message(cmd.conversation_id, cmd.turn_id, "user", cmd.prompt)
                except Exception:
                    logger.exception("Failed persisting daemon user message")
            try:
                async for event in rt.processor.arun_turn(cmd):
                    self._record_turn_event(rt.database, cmd.conversation_id, event)
                    if type(event).__name__ == "TurnCompleted" and not cmd.no_history:
                        response = getattr(event, "response", "")
                        if response:
                            try:
                                rt.history.repo.save_message(cmd.conversation_id, cmd.turn_id, "assistant", response)
                            except Exception:
                                logger.exception("Failed persisting daemon assistant response")
            except Exception as exc:
                self.record_event(rt.database, cmd.conversation_id, "TurnFailed", {"error": str(exc)})

    async def _continue_turn(self, rt, session_id, grant):
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            try:
                for event in rt.processor.continue_turn(grant.turn_id, grant):
                    self._record_turn_event(rt.database, session_id, event)
            except Exception as exc:
                self.record_event(rt.database, session_id, "TurnFailed", {"error": str(exc)})
