"""Local Agent Client Protocol v1 stdio bridge over KITT's canonical turn lifecycle.

ACP requests are untrusted. This bridge does not grant filesystem, terminal,
MCP or automatic approval capabilities. A client can only work in the already
selected workspace; mutations still obey KITT's own host policies.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from kitt import __version__
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import (
    ApprovalRequired, ToolStarted, ToolCompleted, TextDelta, TurnBlocked, TurnCancelled, TurnCompleted, TurnFailed,
)

_MAX_FRAME_BYTES = 1024 * 1024


class ACPServer:
    def __init__(self, runtime):
        self.runtime = runtime
        self._initialized = False
        self._sessions: set[str] = set()
        self._active: dict[str, TurnCommand] = {}
        self._pending_sessions: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._write_lock = asyncio.Lock()
        self._output = None
        self._pending_permissions: dict[str, asyncio.Future] = {}
        self._permission_sessions: dict[str, str] = {}
        self._permission_seq = 0

    async def _send(self, message: dict):
        async with self._write_lock:
            self._output.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._output.flush()

    async def _notification(self, session_id: str, update: dict):
        await self._send({
            "jsonrpc": "2.0", "method": "session/update",
            "params": {"sessionId": session_id, "update": update},
        })

    async def _request_permission(self, session_id: str, event: ApprovalRequired) -> bool:
        """The client only expresses intent. KITT still issues an exact host grant."""
        self._permission_seq += 1
        request_id = f"kitt_permission_{self._permission_seq}"
        future = asyncio.get_running_loop().create_future()
        self._pending_permissions[request_id] = future
        self._permission_sessions[request_id] = session_id
        try:
            await self._send({
                "jsonrpc": "2.0", "id": request_id,
                "method": "session/request_permission",
                "params": {
                    "sessionId": session_id,
                    "toolCall": {
                        "toolCallId": event.turn_id,
                        "title": event.tool_name,
                        "kind": "other",
                        "status": "pending",
                    },
                    "options": [
                        {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
                        {"optionId": "reject-once", "name": "Deny", "kind": "reject_once"},
                    ],
                },
            })
            response = await asyncio.wait_for(future, timeout=120.0)
            return (
                isinstance(response, dict)
                and isinstance(response.get("outcome"), dict)
                and response["outcome"].get("outcome") == "selected"
                and response["outcome"].get("optionId") == "allow-once"
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return False
        finally:
            self._pending_permissions.pop(request_id, None)
            self._permission_sessions.pop(request_id, None)

    def _validate_session(self, params: dict) -> str:
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or session_id not in self._sessions:
            raise ValueError("unknown ACP session")
        return session_id

    async def _prompt(self, request_id, params: dict):
        session_id = self._validate_session(params)
        items = params.get("prompt")
        if not isinstance(items, list) or not items or len(items) > 32:
            raise ValueError("prompt must contain 1..32 content blocks")
        if any(not isinstance(x, dict) or x.get("type") != "text"
               or not isinstance(x.get("text"), str) for x in items):
            raise ValueError("only ACP text prompt blocks are supported")
        prompt = "\n".join(x["text"] for x in items).strip()
        if not prompt or len(prompt.encode("utf-8")) > 64 * 1024:
            raise ValueError("prompt must contain 1..65536 UTF-8 bytes")
        if session_id in self._active:
            raise RuntimeError("ACP session already has an active prompt")
        cmd = TurnCommand(conversation_id=session_id, prompt=prompt)
        self._active[session_id] = cmd
        streamed = ""
        stopped = "end_turn"
        try:
            async for event in self.runtime.processor.arun_turn(cmd):
                if isinstance(event, TextDelta):
                    chunk = event.delta
                    streamed += chunk
                    await self._notification(session_id, {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": chunk},
                    })
                elif isinstance(event, TurnCompleted):
                    if event.response and event.response.startswith(streamed):
                        suffix = event.response[len(streamed):]
                        if suffix:
                            await self._notification(session_id, {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": suffix},
                            })
                elif isinstance(event, ToolStarted):
                    await self._notification(session_id, {
                        "sessionUpdate": "tool_call", "toolCallId": event.call_id or event.tool_name,
                        "title": event.tool_name, "kind": "other", "status": "in_progress",
                    })
                elif isinstance(event, ToolCompleted):
                    await self._notification(session_id, {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": event.call_id or event.tool_name,
                        "status": "completed" if event.success else "failed",
                    })
                elif isinstance(event, ApprovalRequired):
                    accepted = await self._request_permission(session_id, event)
                    if not accepted:
                        for _ in self.runtime.processor.cancel_turn(
                            cmd.turn_id, "ACP approval denied or timed out", conversation_id=session_id,
                        ):
                            pass
                        stopped = "refusal"
                        break
                    # No client-supplied grant is trusted. Only the host can
                    # issue a nonce-bound authorization for this pending action.
                    grant = self.runtime.approval.issue_grant(
                        event.turn_id, session_id, event.workspace_id,
                        event.action_hash, approval_id=event.approval_request_id,
                    )
                    from kitt.ui.fallback import _stream_iterator
                    async for resumed in _stream_iterator(
                        self.runtime.processor.continue_turn(event.turn_id, grant)
                    ):
                        if isinstance(resumed, TextDelta):
                            streamed += resumed.delta
                            await self._notification(session_id, {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": resumed.delta},
                            })
                        elif isinstance(resumed, TurnCompleted):
                            if resumed.response and resumed.response.startswith(streamed):
                                suffix = resumed.response[len(streamed):]
                                if suffix:
                                    await self._notification(session_id, {
                                        "sessionUpdate": "agent_message_chunk",
                                        "content": {"type": "text", "text": suffix},
                                    })
                        elif isinstance(resumed, (TurnBlocked, TurnFailed)):
                            stopped = "refusal"
                        elif isinstance(resumed, TurnCancelled):
                            stopped = "cancelled"
                    break
                elif isinstance(event, TurnCancelled):
                    stopped = "cancelled"
                    break
                elif isinstance(event, (TurnBlocked, TurnFailed)):
                    stopped = "refusal"
                    break
            await self._send({"jsonrpc": "2.0", "id": request_id,
                              "result": {"stopReason": stopped}})
        except Exception as exc:
            await self._send({"jsonrpc": "2.0", "id": request_id,
                              "error": {"code": -32603, "message": type(exc).__name__}})
        finally:
            self._active.pop(session_id, None)

    def _dispatch(self, method: str, params: dict):
        if method == "initialize":
            if self._initialized or not isinstance(params.get("protocolVersion"), int) or params["protocolVersion"] < 1:
                raise ValueError("ACP protocolVersion 1 expected exactly once")
            self._initialized = True
            return {
                "protocolVersion": 1,
                "agentCapabilities": {
                    "loadSession": True,
                    "promptCapabilities": {"image": False, "audio": False, "embeddedContext": False},
                    "mcpCapabilities": {"http": False, "sse": False},
                },
                "authMethods": [],
                "agentInfo": {"name": "kitt", "title": "KITT Agent CLI", "version": __version__},
            }
        if not self._initialized:
            raise ValueError("ACP initialize must be called first")
        if method == "session/new":
            cwd = params.get("cwd")
            if not isinstance(cwd, str) or not Path(cwd).is_absolute():
                raise ValueError("session/new requires absolute cwd")
            if Path(cwd).resolve() != Path(self.runtime.canonical_root).resolve():
                raise PermissionError("ACP cwd must match the configured workspace root")
            if params.get("mcpServers"):
                raise ValueError("ACP client MCP servers are not supported")
            conversation = self.runtime.history.new_conversation("ACP Session")
            session_id = str(conversation["id"])
            self._sessions.add(session_id)
            return {"sessionId": session_id}
        if method == "session/load":
            cwd = params.get("cwd")
            session_id = params.get("sessionId")
            if not isinstance(cwd, str) or not Path(cwd).is_absolute():
                raise ValueError("session/load requires absolute cwd")
            if Path(cwd).resolve() != Path(self.runtime.canonical_root).resolve():
                raise PermissionError("ACP cwd must match the configured workspace root")
            if not isinstance(session_id, str) or len(session_id) > 128:
                raise ValueError("invalid session ID")
            if params.get("mcpServers"):
                raise ValueError("ACP client MCP servers are not supported")
            repo = self.runtime.history.repo
            conversation = repo.get_conversation(session_id)
            if not conversation or conversation.get("workspace_id") != self.runtime.workspace_id:
                raise ValueError("session does not belong to this workspace")
            self._sessions.add(session_id)
            return {"sessionId": session_id}
        raise KeyError(method)

    async def _replay_history(self, session_id: str):
        """ACP v1 requires full ordered session replay before session/load returns."""
        repo = self.runtime.history.repo
        messages = repo.get_messages_for_conversation(session_id)
        if not isinstance(messages, list):
            raise RuntimeError("session history unavailable")
        for message in messages:
            if not isinstance(message, dict):
                raise RuntimeError("invalid persisted message")
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = message.get("content")
            if not isinstance(content, str):
                raise RuntimeError("persisted ACP message must be text")
            variant = "user_message_chunk" if role == "user" else "agent_message_chunk"
            # Bound each transport frame without truncating the saved conversation.
            for pos in range(0, len(content), 16384):
                await self._notification(session_id, {
                    "sessionUpdate": variant,
                    "content": {"type": "text", "text": content[pos:pos + 16384]},
                })

    async def _on_frame(self, frame):
        if (isinstance(frame, dict) and frame.get("jsonrpc") == "2.0"
                and "method" not in frame and "id" in frame):
            future = self._pending_permissions.get(frame["id"]) if isinstance(frame["id"], str) else None
            if future is not None and not future.done():
                future.set_result(frame.get("result"))
            return
        if not isinstance(frame, dict) or frame.get("jsonrpc") != "2.0":
            await self._send({"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32600, "message": "Invalid JSON-RPC request"}})
            return
        method = frame.get("method")
        params = frame.get("params", {})
        request_id = frame.get("id")
        notification = "id" not in frame
        if not isinstance(method, str) or not isinstance(params, dict):
            if not notification:
                await self._send({"jsonrpc": "2.0", "id": request_id,
                                  "error": {"code": -32600, "message": "Invalid request"}})
            return
        if notification:
            if method == "session/cancel":
                try:
                    session_id = self._validate_session(params)
                    cmd = self._active.get(session_id)
                    if cmd:
                        for _ in self.runtime.processor.cancel_turn(
                            cmd.turn_id, "ACP cancellation", conversation_id=session_id,
                        ):
                            pass
                    for identifier, pending in list(self._pending_permissions.items()):
                        if self._permission_sessions.get(identifier) == session_id and not pending.done():
                            pending.cancel()
                except (ValueError, PermissionError):
                    pass
            return
        if method == "session/prompt":
            try:
                session_id = self._validate_session(params)
                items = params.get("prompt")
                if not isinstance(items, list) or not items or len(items) > 32:
                    raise ValueError("prompt must contain 1..32 content blocks")
                if any(not isinstance(x, dict) or x.get("type") != "text"
                       or not isinstance(x.get("text"), str) for x in items):
                    raise ValueError("only ACP text prompt blocks are supported")
                if not 0 < len("\n".join(x["text"] for x in items).strip().encode("utf-8")) <= 65536:
                    raise ValueError("ACP prompt exceeds text budget")
                if session_id in self._active or session_id in self._pending_sessions:
                    raise ValueError("ACP session already has an active prompt")
            except ValueError as exc:
                await self._send({"jsonrpc": "2.0", "id": request_id,
                                  "error": {"code": -32602, "message": str(exc)}})
                return
            self._pending_sessions.add(session_id)
            task = asyncio.create_task(self._prompt(request_id, params))
            self._tasks.add(task)
            def release(task):
                self._pending_sessions.discard(session_id)
                self._tasks.discard(task)
            task.add_done_callback(release)
            return
        try:
            result = self._dispatch(method, params)
            if method == "session/load":
                session_id = result["sessionId"]
                try:
                    await self._replay_history(session_id)
                except Exception:
                    self._sessions.discard(session_id)
                    raise
                result = {}  # ACP v1 session/load result shape.
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except KeyError:
            response = {"jsonrpc": "2.0", "id": request_id,
                        "error": {"code": -32601, "message": "Method not found"}}
        except (ValueError, PermissionError, RuntimeError) as exc:
            response = {"jsonrpc": "2.0", "id": request_id,
                        "error": {"code": -32602, "message": str(exc)}}
        await self._send(response)

    async def serve(self, input_stream, output_stream):
        self._output = output_stream
        try:
            while True:
                line = await asyncio.to_thread(input_stream.readline, _MAX_FRAME_BYTES + 2)
                if not line:
                    break
                if len(line.encode("utf-8")) > _MAX_FRAME_BYTES or not line.endswith("\n"):
                    await self._send({"jsonrpc": "2.0", "id": None,
                                      "error": {"code": -32600, "message": "Frame too large"}})
                    # Never parse the remainder of a rejected oversized frame.
                    break
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    await self._send({"jsonrpc": "2.0", "id": None,
                                      "error": {"code": -32700, "message": "Parse error"}})
                    continue
                await self._on_frame(frame)
        finally:
            for pending in list(self._pending_permissions.values()):
                if not pending.done():
                    pending.cancel()
            for session_id, cmd in list(self._active.items()):
                for _ in self.runtime.processor.cancel_turn(
                    cmd.turn_id, "ACP connection closed", conversation_id=session_id,
                ):
                    pass
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
