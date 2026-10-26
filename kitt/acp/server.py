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
    ApprovalRequired, TextDelta, TurnBlocked, TurnCancelled, TurnCompleted, TurnFailed,
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

    async def _send(self, message: dict):
        async with self._write_lock:
            self._output.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._output.flush()

    async def _notification(self, session_id: str, update: dict):
        await self._send({
            "jsonrpc": "2.0", "method": "session/update",
            "params": {"sessionId": session_id, "update": update},
        })

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
                elif isinstance(event, ApprovalRequired):
                    # ACP clients do not bypass KITT's approval authority.
                    for _ in self.runtime.processor.cancel_turn(
                        cmd.turn_id, "ACP host approval unavailable", conversation_id=session_id,
                    ):
                        pass
                    stopped = "refusal"
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
                    "loadSession": False,
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
        raise KeyError(method)

    async def _on_frame(self, frame):
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
            for session_id, cmd in list(self._active.items()):
                for _ in self.runtime.processor.cancel_turn(
                    cmd.turn_id, "ACP connection closed", conversation_id=session_id,
                ):
                    pass
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
