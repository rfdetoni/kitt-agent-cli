"""Malformed ACP prompt must receive an explicit JSON-RPC error."""
import asyncio
import io
import json
from types import SimpleNamespace

from kitt.acp.server import ACPServer


def test_acp_malformed_prompt_returns_error_not_silent_task_failure():
    runtime = SimpleNamespace()
    async def check():
        server = ACPServer(runtime)
        out = io.StringIO()
        server._output = out
        server._initialized = True
        server._sessions.add("valid")
        await server._on_frame({
            "jsonrpc": "2.0", "id": 7, "method": "session/prompt",
            "params": {"sessionId": "missing", "prompt": [{"type": "text", "text": "hello"}]},
        })
        return json.loads(out.getvalue().strip())
    assert asyncio.run(check())["error"]["code"] == -32602

def test_acp_rejects_second_prompt_while_first_is_queued():
    async def check():
        server = ACPServer(SimpleNamespace())
        out = io.StringIO()
        server._output = out
        server._initialized = True
        server._sessions.add("session")
        req = {"jsonrpc": "2.0", "method": "session/prompt",
               "params": {"sessionId": "session", "prompt": [{"type": "text", "text": "hello"}]}}
        await server._on_frame({**req, "id": 1})
        await server._on_frame({**req, "id": 2})
        pending = [json.loads(x) for x in out.getvalue().splitlines()]
        # Cancel the fake task before running it: only the scheduling rule is under test.
        for task in server._tasks:
            task.cancel()
        await asyncio.gather(*server._tasks, return_exceptions=True)
        return pending
    assert asyncio.run(check())[0]["error"]["code"] == -32602
