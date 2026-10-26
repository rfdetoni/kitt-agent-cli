"""ACP v1: session boundary, framing and canonical turn integration."""
import asyncio
import io
import json
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
