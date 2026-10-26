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
