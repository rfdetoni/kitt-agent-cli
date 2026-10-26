"""ACP v1: session boundary, framing and canonical turn integration."""
import asyncio
import io
import json
import pytest
from pathlib import Path
from types import SimpleNamespace

from kitt.acp.server import ACPServer
from kitt.core.turn_events import TextDelta, TurnCompleted


def test_acp_stdio_initialization_and_prompt(tmp_path):
    class History:
        def new_conversation(self, title):
            return {"id": "test-session"}
    class Processor:
        async def arun_turn(self, cmd):
            yield TextDelta(delta="Hello")
            yield TurnCompleted(response="Hello world")
        def cancel_turn(self, *args, **kwargs):
            return iter(())
    runtime = SimpleNamespace(canonical_root=tmp_path, history=History(), processor=Processor())
    request = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}},
        {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"cwd": str(tmp_path)}},
        {"jsonrpc": "2.0", "id": 3, "method": "session/prompt",
         "params": {"sessionId": "test-session", "prompt": [{"type": "text", "text": "Hi"}]}},
    ]
    source = io.StringIO("".join(json.dumps(x) + "\n" for x in request))
    sink = io.StringIO()
    asyncio.run(ACPServer(runtime).serve(source, sink))
    events = [json.loads(x) for x in sink.getvalue().splitlines()]
    assert events[0]["result"]["protocolVersion"] == 1
    assert events[1]["result"]["sessionId"] == "test-session"
    assert any(x.get("method") == "session/update" for x in events)
    assert events[-1]["result"]["stopReason"] == "end_turn"


def test_acp_refuses_wrong_workspace_and_uninitialized_session(tmp_path):
    runtime = SimpleNamespace(canonical_root=tmp_path)
    async def run():
        sink = io.StringIO()
        agent = ACPServer(runtime)
        agent._output = sink
        await agent._on_frame({"jsonrpc": "2.0", "id": 1, "method": "session/new",
                              "params": {"cwd": str(tmp_path)}})
        await agent._on_frame({"jsonrpc": "2.0", "id": 2, "method": "initialize",
                              "params": {"protocolVersion": 1}})
        await agent._on_frame({"jsonrpc": "2.0", "id": 3, "method": "session/new",
                              "params": {"cwd": str(tmp_path / "other")}})
        return [json.loads(s) for s in sink.getvalue().splitlines()]
    frames = asyncio.run(run())
    assert frames[0]["error"]["code"] == -32602
    assert frames[-1]["error"]["code"] == -32602

def test_acp_load_requires_matching_persisted_workspace(tmp_path):
    class Repo:
        def get_conversation(self, ident):
            return {"id": ident, "workspace_id": "expected"}
    history = SimpleNamespace(repo=Repo())
    runtime = SimpleNamespace(canonical_root=tmp_path, workspace_id="expected", history=history)
    server = ACPServer(runtime)
    server._initialized = True
    loaded = server._dispatch("session/load", {"sessionId": "known", "cwd": str(tmp_path)})
    assert loaded["sessionId"] == "known"
    assert "known" in server._sessions
    runtime.workspace_id = "another"
    with pytest.raises(ValueError, match="workspace"):
        server._dispatch("session/load", {"sessionId": "known", "cwd": str(tmp_path)})


def test_acp_permission_client_reply_only_authorizes_exact_allow_once():
    from kitt.core.turn_events import ApprovalRequired
    event = ApprovalRequired(turn_id="t", tool_name="patch.apply", workspace_id="w")
    async def exercise(reply):
        server = ACPServer(SimpleNamespace())
        sink = io.StringIO()
        server._output = sink
        task = asyncio.create_task(server._request_permission("s", event))
        while not server._pending_permissions:
            await asyncio.sleep(0)
        request = json.loads(sink.getvalue().splitlines()[-1])
        await server._on_frame({"jsonrpc": "2.0", "id": request["id"],
                                "result": {"outcome": {"outcome": "selected", "optionId": reply}}})
        return await task
    assert asyncio.run(exercise("allow-once")) is True
    assert asyncio.run(exercise("reject-once")) is False
