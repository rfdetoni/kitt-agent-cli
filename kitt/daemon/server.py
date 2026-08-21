from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import re
import secrets
import stat
import sys
import time
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from kitt.core.runtime import KittRuntime
from kitt.core.turn_command import TurnCommand
from kitt.daemon.protocol import DaemonEvent, decode_line, encode_message
from kitt.daemon.redaction import sanitize_public_event_payload
from kitt.daemon.transport import IPCTransport
from kitt.history.database import HistoryDatabase
from kitt.tools.approval import ApprovalGrant
from kitt.security.capabilities import capabilities_for_tools
from kitt.security.context import ExecutionSecurityContext

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
        self._runtime_event_unsubscribers = {}
        self._active_turns: Dict[str, str] = {}
        self._direct_pending: Dict[str, dict[str, Any]] = {}
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
            self._attach_runtime_event_bridge(root, rt)
        rt = self._runtimes[root]
        await rt.start()
        return rt

    def _attach_runtime_event_bridge(self, root: str, rt) -> None:
        loop = asyncio.get_running_loop()
        public_child_events = {
            "ChildAgentSpawned",
            "ChildAgentProgress",
            "ChildAgentFinished",
            "ChildAgentApprovalContinued",
        }

        def on_event(event_name: str, payload: dict[str, Any]) -> None:
            if event_name not in public_child_events:
                return
            child_id = str((payload or {}).get("child_id") or "")
            if not child_id:
                return
            child = rt.children.repo.get(child_id)
            if not child:
                return
            session_id = str(child.parent_conversation_id or "")
            if not session_id:
                return
            try:
                loop.call_soon_threadsafe(
                    self.record_event,
                    rt.database,
                    session_id,
                    event_name,
                    dict(payload or {}),
                )
            except RuntimeError:
                # Daemon loop is shutting down; persisted runtime shutdown wins.
                return

        self._runtime_event_unsubscribers[root] = rt.events.subscribe("*", on_event)

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
            for unsubscribe in list(self._runtime_event_unsubscribers.values()):
                try:
                    unsubscribe()
                except Exception:
                    pass
            self._runtime_event_unsubscribers.clear()
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
        for unsubscribe in list(self._runtime_event_unsubscribers.values()):
            try:
                unsubscribe()
            except Exception:
                pass
        self._runtime_event_unsubscribers.clear()
        for rt in list(self._runtimes.values()):
            try:
                await rt.aclose()
            except Exception:
                logger.exception("Runtime shutdown failure")
        self._runtimes.clear()
        self._active_turns.clear()
        self._direct_pending.clear()
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
                "trusted": ext.mcp.is_trusted(cfg.server_id),
                "source": cfg.source,
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
        if action == "mcp.trust":
            if not server_id:
                raise ValueError("MCP server name is required")
            digest = ext.mcp.trust_server(server_id)
            return {
                "server_id": server_id,
                "trusted": True,
                "digest": digest,
            }
        if action == "mcp.untrust":
            if not server_id:
                raise ValueError("MCP server name is required")
            removed = await ext.mcp.untrust_server(server_id)
            return {
                "server_id": server_id,
                "trusted": False,
                "removed": removed,
            }
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

    @staticmethod
    def _safe_json_preview(value: Any, max_chars: int = 24_000) -> Any:
        """Bound payloads exposed through management/remote-control APIs."""
        value = _jsonable(value)
        try:
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            return str(value)[:max_chars]
        if len(raw) <= max_chars:
            return value
        return {"_truncated": True, "preview": raw[:max_chars]}

    def _require_session(self, rt, session_id: str) -> dict[str, Any]:
        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("session_id is required")
        conv = rt.history.repo.get_conversation(sid)
        if not conv:
            raise ValueError(f"Unknown session '{sid}'")
        if conv.get("workspace_id") != rt.workspace_id:
            raise ValueError("Cross-workspace session access blocked")
        return conv

    @staticmethod
    def _encode_message_cursor(created_at: float, row_id: str) -> str:
        return f"{float(created_at):.17g}:{row_id}"

    @staticmethod
    def _decode_message_cursor(value: Any) -> Optional[tuple[float, str]]:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            created, row_id = raw.rsplit(":", 1)
            created_at = float(created)
        except (TypeError, ValueError):
            raise ValueError("Invalid message cursor")
        if not row_id or len(row_id) > 128 or not re.fullmatch(r"[A-Za-z0-9_-]+", row_id):
            raise ValueError("Invalid message cursor")
        return created_at, row_id

    def _require_active_turn(self, session_id: str, turn_id: str) -> None:
        sid = str(session_id or "").strip()
        tid = str(turn_id or "").strip()
        if not sid or not tid:
            raise ValueError("session_id and turn_id are required")
        if self._active_turns.get(tid) != sid:
            raise ValueError("Turn is not active in the requested session")

    def _prune_direct_pending(self, rt) -> None:
        now = time.time()
        expired = [
            approval_id
            for approval_id, item in self._direct_pending.items()
            if float(item.get("expires_at", 0)) <= now
        ]
        for approval_id in expired:
            self._direct_pending.pop(approval_id, None)
            try:
                rt.approval.deny(approval_id, "Direct UI approval expired")
            except Exception:
                pass
        if len(self._direct_pending) > 64:
            oldest = sorted(
                self._direct_pending.items(),
                key=lambda pair: float(pair[1].get("created_at", 0)),
            )[: len(self._direct_pending) - 64]
            for approval_id, _ in oldest:
                self._direct_pending.pop(approval_id, None)
                try:
                    rt.approval.deny(approval_id, "Direct UI approval capacity exceeded")
                except Exception:
                    pass

    async def _ui_tool_execute(self, rt, session_id: str, tool_name: str, args: dict) -> dict[str, Any]:
        self._require_session(rt, session_id)
        allowed = {"run_command", "child_spawn"}
        tool_name = str(tool_name or "").strip()
        if tool_name not in allowed:
            raise ValueError(f"Direct UI tool '{tool_name}' is not allowed")
        if not isinstance(args, dict):
            raise ValueError("Direct UI tool args must be an object")
        turn_id = f"ui_{uuid.uuid4().hex}"
        security_context = ExecutionSecurityContext.create_user_context(
            workspace_id=rt.workspace_id,
            conversation_id=session_id,
            turn_id=turn_id,
            capabilities=capabilities_for_tools([tool_name], strict=True),
        )
        self.record_event(
            rt.database, session_id, "ToolStarted",
            {"tool_name": tool_name, "args": self._safe_json_preview(args), "call_id": turn_id},
        )
        result = await asyncio.to_thread(
            rt.registry.execute_tool,
            tool_name, args, turn_id, session_id, rt.workspace_id,
            None, None, None, "USER", security_context,
        )
        if result.requires_approval:
            self._prune_direct_pending(rt)
            if len(self._direct_pending) >= 64:
                raise RuntimeError("Too many pending direct UI approvals")
            action_hash = rt.policy.generate_action_hash(tool_name, args)
            approval_id = f"req_{turn_id}_{action_hash[:8]}"
            request = rt.approval.register_request(
                turn_id, session_id, rt.workspace_id, action_hash, approval_id,
                tool_name=tool_name, summary=f"Direct UI {tool_name}",
            )
            self._direct_pending[approval_id] = {
                "approval_id": approval_id,
                "turn_id": turn_id,
                "conversation_id": session_id,
                "workspace_id": rt.workspace_id,
                "tool_name": tool_name,
                "args": dict(args),
                "action_hash": action_hash,
                "security_context": security_context,
                "created_at": time.time(),
                "expires_at": float(request.expires_at),
            }
            self.record_event(
                rt.database, session_id, "ApprovalRequired",
                {
                    "turn_id": turn_id,
                    "conversation_id": session_id,
                    "tool_name": tool_name,
                    "args": self._safe_json_preview(args),
                    "action_hash": action_hash,
                    "approval_request_id": approval_id,
                    "workspace_id": rt.workspace_id,
                },
            )
            return {
                "success": False,
                "requires_approval": True,
                "approval_id": approval_id,
                "turn_id": turn_id,
            }
        self.record_event(
            rt.database, session_id, "ToolCompleted",
            {
                "tool_name": tool_name,
                "success": bool(result.success),
                "output": str(result.output or "")[:64 * 1024],
                "error": result.error,
                "call_id": turn_id,
            },
        )
        return {
            "success": bool(result.success),
            "requires_approval": False,
            "output": str(result.output or "")[:64 * 1024],
            "error": result.error,
        }

    async def _resolve_direct_approval(self, rt, item: dict[str, Any], allow: bool) -> dict[str, Any]:
        approval_id = str(item["approval_id"])
        session_id = str(item["conversation_id"])
        turn_id = str(item["turn_id"])
        if not allow:
            rt.approval.deny(approval_id, "Denied via daemon UI")
            self._direct_pending.pop(approval_id, None)
            self.record_event(
                rt.database, session_id, "ToolCompleted",
                {
                    "tool_name": item["tool_name"], "success": False,
                    "output": "", "error": "Approval denied", "call_id": turn_id,
                },
            )
            return {"decision": "denied", "approval_id": approval_id, "session_id": session_id, "turn_id": turn_id}
        grant = rt.approval.issue_grant(
            turn_id=turn_id, conversation_id=session_id, workspace_id=rt.workspace_id,
            action_hash=str(item["action_hash"]), approval_id=approval_id,
        )
        if grant is None:
            raise RuntimeError("Direct UI approval could not be granted")
        result = await asyncio.to_thread(
            rt.registry.execute_tool,
            item["tool_name"], item["args"], turn_id, session_id, rt.workspace_id,
            None, grant, approval_id, "USER", item["security_context"],
        )
        self._direct_pending.pop(approval_id, None)
        self.record_event(
            rt.database, session_id, "ToolCompleted",
            {
                "tool_name": item["tool_name"], "success": bool(result.success),
                "output": str(result.output or "")[:64 * 1024], "error": result.error,
                "call_id": turn_id,
            },
        )
        return {
            "decision": "approved", "approval_id": approval_id,
            "session_id": session_id, "turn_id": turn_id,
            "success": bool(result.success), "output": str(result.output or "")[:64 * 1024],
            "error": result.error,
        }

    def _approval_payloads(
        self,
        rt,
        session_id: Optional[str] = None,
        approval_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        now = time.time()
        clauses = [
            "a.workspace_id=?",
            "a.state='PENDING'",
            "CAST(a.expires_at AS REAL)>?",
            "p.id IS NOT NULL",
        ]
        params: list[Any] = [rt.workspace_id, now]
        if session_id:
            self._require_session(rt, session_id)
            clauses.append("a.conversation_id=?")
            params.append(str(session_id))
        if approval_id:
            clauses.append("a.approval_id=?")
            params.append(str(approval_id))
        params.append(max(1, min(int(limit), 100)))
        sql = f"""
            SELECT
                a.approval_id,a.conversation_id,a.turn_id,a.workspace_id,
                a.tool_name,a.arguments_hash,a.risk_level,a.requested_at,a.expires_at,
                p.normalized_args_json,p.affected_paths_json,p.action_hash
            FROM approval_requests a
            LEFT JOIN pending_actions p
              ON p.approval_request_id=a.approval_id AND p.state='pending'
            WHERE {' AND '.join(clauses)}
            ORDER BY CAST(a.requested_at AS REAL) DESC
            LIMIT ?
        """
        with rt.database.get_connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                arguments = json.loads(row["normalized_args_json"] or "{}")
            except Exception:
                arguments = {}
            try:
                affected_paths = json.loads(row["affected_paths_json"] or "[]")
            except Exception:
                affected_paths = []
            result.append(
                {
                    "approval_id": row["approval_id"],
                    "conversation_id": row["conversation_id"],
                    "turn_id": row["turn_id"],
                    "workspace_id": row["workspace_id"],
                    "tool_name": row["tool_name"],
                    "action_hash": row["action_hash"] or row["arguments_hash"],
                    "risk_level": row["risk_level"],
                    "requested_at": float(row["requested_at"] or 0),
                    "expires_at": float(row["expires_at"] or 0),
                    "arguments": self._safe_json_preview(arguments),
                    "affected_paths": [str(p) for p in affected_paths[:100]],
                }
            )
        self._prune_direct_pending(rt)
        for item in self._direct_pending.values():
            if session_id and item.get("conversation_id") != session_id:
                continue
            if approval_id and item.get("approval_id") != approval_id:
                continue
            result.append({
                "approval_id": item["approval_id"],
                "conversation_id": item["conversation_id"],
                "turn_id": item["turn_id"],
                "workspace_id": item["workspace_id"],
                "tool_name": item["tool_name"],
                "action_hash": item["action_hash"],
                "risk_level": "MEDIUM",
                "requested_at": item["created_at"],
                "expires_at": item["expires_at"],
                "arguments": self._safe_json_preview(item["args"]),
                "affected_paths": [],
                "direct_ui": True,
            })
        result.sort(key=lambda row: float(row.get("requested_at", 0)), reverse=True)
        return result[: max(1, min(int(limit), 100))]

    def _session_detail(
        self,
        rt,
        session_id: str,
        *,
        message_limit: int = 50,
        before: Any = None,
        include_events: bool = True,
        event_limit: int = 40,
    ) -> dict[str, Any]:
        conv = self._require_session(rt, session_id)
        message_limit = max(1, min(int(message_limit), 100))
        event_limit = max(1, min(int(event_limit), 80))
        cursor = self._decode_message_cursor(before)
        message_params: list[Any] = [session_id]
        cursor_sql = ""
        if cursor:
            cursor_sql = " AND (created_at < ? OR (created_at = ? AND id < ?))"
            message_params.extend([cursor[0], cursor[0], cursor[1]])
        message_params.append(message_limit + 1)
        with rt.database.get_connection() as conn:
            message_rows = conn.execute(
                f"""SELECT id,role,content,created_at,turn_id
                    FROM messages WHERE conversation_id=?{cursor_sql}
                    ORDER BY created_at DESC,id DESC LIMIT ?""",
                tuple(message_params),
            ).fetchall()
            sequence_row = conn.execute(
                "SELECT COALESCE(MAX(id),0) AS seq FROM daemon_events WHERE session_id=?",
                (session_id,),
            ).fetchone()
            event_rows = (
                conn.execute(
                    """SELECT id,session_id,event_type,payload_json,created_at
                       FROM daemon_events WHERE session_id=?
                       ORDER BY id DESC LIMIT ?""",
                    (session_id, event_limit + 1),
                ).fetchall()
                if include_events else []
            )

        message_budget = 512 * 1024
        used_message_bytes = 0
        selected_messages = []
        budget_truncated = False
        raw_messages = list(message_rows[:message_limit])
        for row in raw_messages:
            content = str(row["content"] or "")
            encoded = content.encode("utf-8", errors="replace")
            if len(encoded) > 64 * 1024:
                encoded = encoded[: 64 * 1024]
                content = encoded.decode("utf-8", errors="replace") + "…[truncated]"
            cost = len(encoded) + 512
            if selected_messages and used_message_bytes + cost > message_budget:
                budget_truncated = True
                break
            used_message_bytes += cost
            selected_messages.append(
                {
                    "id": row["id"], "role": row["role"], "content": content,
                    "created_at": row["created_at"], "turn_id": row["turn_id"],
                }
            )
        messages_has_more = len(message_rows) > message_limit or budget_truncated
        next_before = (
            self._encode_message_cursor(
                selected_messages[-1]["created_at"], selected_messages[-1]["id"]
            )
            if messages_has_more and selected_messages else ""
        )
        messages = list(reversed(selected_messages))

        recent_events = []
        event_budget = 384 * 1024
        used_event_bytes = 0
        for row in reversed(event_rows[:event_limit]):
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except Exception:
                payload = {}
            payload = sanitize_public_event_payload(row["event_type"], payload)
            payload = self._safe_json_preview(payload, max_chars=12_000)
            encoded_size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 256
            if recent_events and used_event_bytes + encoded_size > event_budget:
                break
            used_event_bytes += encoded_size
            recent_events.append(
                {
                    "sequence_id": int(row["id"]),
                    "session_id": row["session_id"],
                    "event_type": row["event_type"],
                    "payload": payload,
                    "created_at": row["created_at"],
                }
            )
        return {
            "conversation": {
                "id": conv.get("id"), "title": conv.get("title"),
                "status": conv.get("status"), "created_at": conv.get("created_at"),
                "updated_at": conv.get("updated_at"),
            },
            "messages": messages,
            "messages_has_more": messages_has_more,
            "messages_next_before": next_before,
            "messages_budget_truncated": budget_truncated,
            "recent_events": recent_events,
            "last_sequence": int(sequence_row["seq"] if sequence_row else 0),
            "approvals": self._approval_payloads(rt, session_id=session_id) if include_events else [],
        }

    @staticmethod
    def _artifact_metadata(artifact) -> dict[str, Any]:
        return {
            "id": artifact.id,
            "conversation_id": artifact.conversation_id,
            "turn_id": artifact.turn_id,
            "artifact_type": artifact.artifact_type,
            "summary": artifact.summary,
            "content_hash": artifact.content_hash,
            "size_bytes": artifact.size_bytes,
            "sensitivity": artifact.sensitivity,
            "created_at": artifact.created_at,
            "expires_at": artifact.expires_at,
            "pinned": artifact.pinned,
            "metadata": DaemonServer._safe_json_preview(artifact.metadata),
        }

    def _artifact_list(self, rt, session_id: str) -> list[dict[str, Any]]:
        self._require_session(rt, session_id)
        artifacts = rt.artifacts.list(
            conversation_id=session_id,
            workspace_id=rt.workspace_id,
            limit=50,
        )
        return [self._artifact_metadata(item) for item in artifacts]

    def _artifact_read(
        self,
        rt,
        session_id: str,
        artifact_id: str,
        offset: int,
    ) -> dict[str, Any]:
        self._require_session(rt, session_id)
        artifact = rt.artifacts.get(str(artifact_id or ""))
        if (
            artifact is None
            or artifact.workspace_id != rt.workspace_id
            or artifact.conversation_id != session_id
        ):
            raise ValueError("Artifact not found in this session")
        page = rt.artifacts.read_text_page(
            artifact.id,
            offset=max(0, int(offset)),
            max_bytes=min(32 * 1024, rt.artifacts.page_bytes),
        )
        return {"artifact": self._artifact_metadata(artifact), **page}

    def _workspace_diff(self) -> dict[str, Any]:
        """Return a bounded, read-only Git diff using a fixed argv."""
        import subprocess
        from kitt.tools.process_runner import sanitized_subprocess_env

        commands = [
            [
                "git", "-C", str(self.workspace_root), "diff",
                "--no-ext-diff", "--no-color", "HEAD", "--",
            ],
            [
                "git", "-C", str(self.workspace_root), "diff",
                "--no-ext-diff", "--no-color", "--",
            ],
        ]
        last_error = ""
        for command in commands:
            try:
                completed = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=sanitized_subprocess_env(),
                    timeout=5.0,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                last_error = str(exc)
                continue
            if completed.returncode != 0:
                last_error = completed.stderr.decode("utf-8", errors="replace")[:4096]
                continue
            raw = completed.stdout
            max_bytes = 256 * 1024
            truncated = len(raw) > max_bytes
            if truncated:
                raw = raw[:max_bytes]
            return {
                "available": True,
                "content": raw.decode("utf-8", errors="replace"),
                "truncated": truncated,
                "bytes_returned": len(raw),
            }
        return {
            "available": False,
            "content": "",
            "truncated": False,
            "error": last_error or "Git diff unavailable",
        }

    async def _approval_action(
        self,
        rt,
        action: str,
        approval_id: str,
        session_id: Optional[str],
    ) -> dict[str, Any]:
        approval_id = str(approval_id or "").strip()
        if not approval_id:
            raise ValueError("approval_id is required")
        self._prune_direct_pending(rt)
        direct = self._direct_pending.get(approval_id)
        if direct is not None:
            if session_id and str(direct.get("conversation_id")) != str(session_id):
                raise ValueError("Approval does not belong to requested session")
            return await self._resolve_direct_approval(
                rt, direct, action == "approval.approve"
            )
        pending = self._approval_payloads(
            rt,
            session_id=session_id,
            approval_id=approval_id,
            limit=1,
        )
        if not pending:
            raise ValueError("Approval is unknown, expired, or no longer pending")
        item = pending[0]
        sid = str(item["conversation_id"])
        if action == "approval.approve":
            grant = rt.approval.issue_grant(
                turn_id=str(item["turn_id"]),
                conversation_id=sid,
                workspace_id=str(item["workspace_id"]),
                action_hash=str(item["action_hash"]),
                approval_id=approval_id,
            )
            if grant is None:
                raise RuntimeError("Approval could not be granted; it may have changed concurrently")
            # The grant nonce stays inside the daemon and is never exposed to
            # the HTTP/browser boundary.
            asyncio.create_task(self._continue_turn(rt, sid, grant))
            return {
                "approval_id": approval_id,
                "session_id": sid,
                "turn_id": item["turn_id"],
                "decision": "approved",
            }
        if action == "approval.deny":
            if not rt.approval.deny(approval_id, "Denied via KITT remote web"):
                raise RuntimeError("Approval could not be denied")
            for event in rt.processor.cancel_turn(
                str(item["turn_id"]),
                "Denied via KITT remote web",
                conversation_id=sid,
            ):
                self._record_turn_event(rt.database, sid, event)
            self._active_turns.pop(str(item["turn_id"]), None)
            return {
                "approval_id": approval_id,
                "session_id": sid,
                "turn_id": item["turn_id"],
                "decision": "denied",
            }
        raise ValueError(f"Unsupported approval action '{action}'")

    async def _astream_blocking(self, iterator_factory, thread_name: str):
        """Bridge a synchronous event iterator without blocking the daemon loop."""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=128)
        stop = threading.Event()

        def put(kind: str, value: Any = None, timeout: float = 30.0) -> bool:
            if stop.is_set():
                return False
            future = asyncio.run_coroutine_threadsafe(q.put((kind, value)), loop)
            try:
                future.result(timeout=timeout)
                return True
            except Exception:
                future.cancel()
                return False

        def produce() -> None:
            iterator = None
            try:
                iterator = iterator_factory()
                for event in iterator:
                    if stop.is_set() or not put("event", event):
                        break
            except BaseException as exc:
                if not stop.is_set():
                    put("error", exc, timeout=5.0)
            finally:
                if iterator is not None and hasattr(iterator, "close"):
                    try:
                        iterator.close()
                    except Exception:
                        pass
                if not stop.is_set():
                    put("done", None, timeout=5.0)

        threading.Thread(target=produce, name=thread_name, daemon=True).start()
        try:
            while True:
                kind, value = await q.get()
                if kind == "done":
                    break
                if kind == "error":
                    if isinstance(value, Exception):
                        raise value
                    raise RuntimeError(str(value))
                yield value
        finally:
            stop.set()

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

                if not self._workspace_allowed(msg.get("workspace")):
                    await q.put(encode_message({
                        "type": "RESPONSE",
                        "request_id": req_id,
                        "status": "error",
                        "error": "Cross-workspace daemon request blocked",
                    }))
                    continue

                rt = await self._get_or_create_runtime(
                    str(self.workspace_root)
                )

                if action == "runtime.status":
                    snapshot = rt.snapshot()
                    await q.put(encode_message({
                        "type": "RESPONSE",
                        "request_id": req_id,
                        "status": "ok",
                        "action": action,
                        "workspace_root": str(self.workspace_root),
                        "workspace_id": rt.workspace_id,
                        "snapshot": _jsonable(snapshot),
                        "pending_approvals": len(self._approval_payloads(rt)),
                    }))
                elif action == "get_session":
                    try:
                        payload = self._session_detail(
                            rt,
                            str(msg.get("session_id", "")),
                            message_limit=max(1, min(int(msg.get("message_limit", 50)), 100)),
                            before=msg.get("before"),
                            include_events=bool(msg.get("include_events", True)),
                            event_limit=max(1, min(int(msg.get("event_limit", 40)), 80)),
                        )
                    except Exception as exc:
                        await q.put(encode_message({
                            "type": "RESPONSE", "request_id": req_id,
                            "status": "error", "error": str(exc),
                        }))
                        continue
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id,
                        "status": "ok", "action": action, **payload,
                    }))
                elif action == "events_since":
                    sid = str(msg.get("session_id", ""))
                    try:
                        self._require_session(rt, sid)
                        last_sequence = max(0, int(msg.get("last_sequence", 0)))
                        limit = max(1, min(int(msg.get("limit", 200)), 500))
                        events, more, next_seq = self._get_events_since(
                            rt.database, sid, last_sequence, limit=limit
                        )
                    except Exception as exc:
                        await q.put(encode_message({
                            "type": "RESPONSE", "request_id": req_id,
                            "status": "error", "error": str(exc),
                        }))
                        continue
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id,
                        "status": "ok", "action": action,
                        "session_id": sid,
                        "events": [e.to_dict() for e in events],
                        "has_more": more,
                        "next_sequence": next_seq,
                    }))
                elif action == "artifact.list":
                    try:
                        artifacts = self._artifact_list(
                            rt, str(msg.get("session_id", ""))
                        )
                    except Exception as exc:
                        await q.put(encode_message({
                            "type": "RESPONSE", "request_id": req_id,
                            "status": "error", "error": str(exc),
                        }))
                        continue
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id,
                        "status": "ok", "action": action,
                        "artifacts": artifacts,
                    }))
                elif action == "artifact.read":
                    try:
                        payload = self._artifact_read(
                            rt,
                            str(msg.get("session_id", "")),
                            str(msg.get("artifact_id", "")),
                            max(0, int(msg.get("offset", 0))),
                        )
                    except Exception as exc:
                        await q.put(encode_message({
                            "type": "RESPONSE", "request_id": req_id,
                            "status": "error", "error": str(exc),
                        }))
                        continue
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id,
                        "status": "ok", "action": action, **payload,
                    }))
                elif action == "workspace.diff":
                    payload = self._workspace_diff()
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id,
                        "status": "ok", "action": action, **payload,
                    }))
                elif action == "approval.list":
                    try:
                        approvals = self._approval_payloads(
                            rt,
                            session_id=(str(msg.get("session_id", "")).strip() or None),
                        )
                    except Exception as exc:
                        await q.put(encode_message({
                            "type": "RESPONSE", "request_id": req_id,
                            "status": "error", "error": str(exc),
                        }))
                        continue
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id,
                        "status": "ok", "action": action,
                        "approvals": approvals,
                    }))
                elif action in {"approval.approve", "approval.deny"}:
                    try:
                        payload = await self._approval_action(
                            rt,
                            action,
                            str(msg.get("approval_id", "")),
                            (str(msg.get("session_id", "")).strip() or None),
                        )
                    except Exception as exc:
                        await q.put(encode_message({
                            "type": "RESPONSE", "request_id": req_id,
                            "status": "error", "error": str(exc),
                        }))
                        continue
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id,
                        "status": "ok", "action": action, **payload,
                    }))
                elif action == "ui.tool.execute":
                    try:
                        payload = await self._ui_tool_execute(
                            rt, str(msg.get("session_id", "")),
                            str(msg.get("tool_name", "")), msg.get("args") or {},
                        )
                    except Exception as exc:
                        await q.put(encode_message({
                            "type": "RESPONSE", "request_id": req_id,
                            "status": "error", "error": str(exc),
                        }))
                        continue
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id, "status": "ok", **payload,
                    }))
                elif action == "ui.undo":
                    sid = str(msg.get("session_id", ""))
                    try:
                        self._require_session(rt, sid)
                        changeset = await asyncio.to_thread(
                            rt.processor.diff_applier.tracker.revert_last_changeset
                        )
                    except Exception as exc:
                        await q.put(encode_message({
                            "type": "RESPONSE", "request_id": req_id,
                            "status": "error", "error": str(exc),
                        }))
                        continue
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id, "status": "ok",
                        "reverted": changeset is not None,
                        "changeset_id": getattr(changeset, "id", None),
                    }))
                elif action == "runtime.set_reasoning":
                    value = max(0, min(100, int(msg.get("value", 50))))
                    rt.processor.reasoning_effort = value
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id, "status": "ok", "value": value,
                    }))
                elif action == "runtime.set_autonomy":
                    try:
                        policy = rt.autonomy_store.set_preset(str(msg.get("preset", "supervised")))
                        rt.processor.registry.policy.autonomy = policy
                    except Exception as exc:
                        await q.put(encode_message({
                            "type": "RESPONSE", "request_id": req_id, "status": "error", "error": str(exc),
                        }))
                        continue
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id, "status": "ok",
                        "preset": policy.level,
                    }))
                elif action == "runtime.reload_router":
                    rt.processor.router.config = rt.processor.router.load_config(str(self.workspace_root))
                    if hasattr(rt.processor.registry, "register_custom_provider"):
                        for custom in getattr(rt.processor.router.config, "custom_providers", []) or []:
                            rt.processor.registry.register_custom_provider(
                                provider_id=custom.get("name", ""),
                                name=custom.get("name", ""),
                                protocol=custom.get("protocol", "openai-chat-completions"),
                                base_url=custom.get("base_url", ""),
                            )
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id, "status": "ok",
                    }))
                elif action == "approval.remember":
                    try:
                        decision = str(msg.get("decision", "allow"))
                        scope = str(msg.get("scope", "session"))
                        rt.approval.remember(
                            str(msg.get("tool_name", "")),
                            str(msg.get("path_glob", "**")),
                            decision, scope,
                        )
                    except Exception as exc:
                        await q.put(encode_message({
                            "type": "RESPONSE", "request_id": req_id, "status": "error", "error": str(exc),
                        }))
                        continue
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id, "status": "ok",
                    }))
                elif action == "list_sessions":
                    convs = rt.history.list_history(limit=50)
                    active = rt.history.get_active_read_only()
                    await q.put(encode_message({
                        "type": "RESPONSE", "request_id": req_id, "status": "ok",
                        "active_session_id": active.get("id") if active else "",
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
                    self._active_turns[cmd.turn_id] = sid
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
                    try:
                        if grant.conversation_id != sid:
                            raise ValueError("Grant does not belong to requested session")
                        self._require_active_turn(sid, grant.turn_id)
                    except Exception as exc:
                        await q.put(encode_message({
                            "type": "RESPONSE", "request_id": req_id,
                            "status": "error", "error": str(exc),
                        }))
                        continue
                    asyncio.create_task(self._continue_turn(rt, sid, grant))
                    await q.put(encode_message({"type": "RESPONSE", "request_id": req_id, "status": "ok"}))
                elif action == "cancel_turn":
                    sid = str(msg.get("session_id", ""))
                    turn_id = str(msg.get("turn_id", ""))
                    try:
                        self._require_session(rt, sid)
                        self._require_active_turn(sid, turn_id)
                    except Exception as exc:
                        await q.put(encode_message({
                            "type": "RESPONSE", "request_id": req_id,
                            "status": "error", "error": str(exc),
                        }))
                        continue
                    for event in rt.processor.cancel_turn(
                        turn_id, "Cancelled via daemon IPC", conversation_id=sid
                    ):
                        self._record_turn_event(rt.database, sid, event)
                    self._active_turns.pop(turn_id, None)
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
                    "mcp.trust",
                    "mcp.untrust",
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
                payload=sanitize_public_event_payload(
                    r["event_type"], json.loads(r["payload_json"] or "{}")
                ),
                created_at=r["created_at"],
            )
            events.append(evt)
            next_seq = max(next_seq, r["id"])
        return events, more, next_seq

    def record_event(self, db, session_id, event_type, payload):
        now = time.time()
        payload = sanitize_public_event_payload(event_type, _jsonable(payload))
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
            paused_for_approval = False
            if not cmd.no_history:
                try:
                    rt.history.repo.save_message(cmd.conversation_id, cmd.turn_id, "user", cmd.prompt)
                except Exception:
                    logger.exception("Failed persisting daemon user message")
            try:
                async for event in rt.processor.arun_turn(cmd):
                    event_name = type(event).__name__
                    self._record_turn_event(rt.database, cmd.conversation_id, event)
                    if event_name == "ApprovalRequired":
                        paused_for_approval = True
                    if event_name in {"TurnCompleted", "TurnFailed", "TurnCancelled", "TurnBlocked"}:
                        self._active_turns.pop(cmd.turn_id, None)
                    if event_name == "TurnCompleted" and not cmd.no_history:
                        response = getattr(event, "response", "")
                        if response:
                            try:
                                rt.history.repo.save_message(cmd.conversation_id, cmd.turn_id, "assistant", response)
                            except Exception:
                                logger.exception("Failed persisting daemon assistant response")
            except Exception as exc:
                self._active_turns.pop(cmd.turn_id, None)
                self.record_event(rt.database, cmd.conversation_id, "TurnFailed", {"error": str(exc)})
            finally:
                if not paused_for_approval:
                    self._active_turns.pop(cmd.turn_id, None)

    async def _continue_turn(self, rt, session_id, grant):
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            self._active_turns[grant.turn_id] = session_id
            paused_for_approval = False
            try:
                async for event in self._astream_blocking(
                    lambda: rt.processor.continue_turn(grant.turn_id, grant),
                    f"kitt-continue-{grant.turn_id[:8]}",
                ):
                    event_name = type(event).__name__
                    self._record_turn_event(rt.database, session_id, event)
                    if event_name == "ApprovalRequired":
                        paused_for_approval = True
                    if event_name in {"TurnCompleted", "TurnFailed", "TurnCancelled", "TurnBlocked"}:
                        self._active_turns.pop(grant.turn_id, None)
            except Exception as exc:
                self._active_turns.pop(grant.turn_id, None)
                self.record_event(rt.database, session_id, "TurnFailed", {"error": str(exc)})
            finally:
                if not paused_for_approval:
                    self._active_turns.pop(grant.turn_id, None)
