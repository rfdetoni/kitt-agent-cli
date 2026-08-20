from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kitt.extensions.manager import ExtensionManager
from kitt.extensions.manifest import parse_manifest_file
from kitt.extensions.mcp.manager import MCPManager
from kitt.extensions.mcp.models import MCPServerConfig, MCPTool
from kitt.extensions.mcp.tool_adapter import MCPToolAdapter
from kitt.extensions.mcp.transport import HTTPTransport
from kitt.extensions.plugins.loader import PluginLoader
from kitt.extensions.plugins.registry import PluginRegistry
from kitt.extensions.plugins.security import (
    PluginStateStore,
    PluginTrustStore,
)


class TestPluginReloadCleanup(unittest.IsolatedAsyncioTestCase):
    async def test_unload_removes_digest_scoped_modules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin = root / ".kitt" / "plugins" / "demo"
            plugin.mkdir(parents=True)
            (plugin / "helper.py").write_text(
                "VALUE = 1\n", encoding="utf-8"
            )
            (plugin / "plugin.py").write_text(
                "from .helper import VALUE\n"
                "def setup(ctx):\n"
                "    return None\n",
                encoding="utf-8",
            )
            (plugin / "plugin.toml").write_text(
                "\n".join(
                    [
                        'name = "demo"',
                        'version = "1.0.0"',
                        'api_version = "1"',
                        'entrypoint = "plugin:setup"',
                        "permissions = []",
                        "trusted_in_process = true",
                    ]
                ),
                encoding="utf-8",
            )
            manifest = parse_manifest_file(plugin / "plugin.toml")
            trust = PluginTrustStore(
                root, path=root / "trust.json"
            )
            trust.grant(manifest)
            loader = PluginLoader(
                workspace_root=str(root),
                trust_store=trust,
            )
            registry = PluginRegistry(
                loader=loader,
                state_store=PluginStateStore(
                    root, path=root / "state.json"
                ),
            )
            registry.discover()
            await registry.start("demo")
            instance = registry.get("demo")
            self.assertIsNotNone(instance)
            prefix = instance.module_prefix
            self.assertTrue(
                any(
                    name == prefix
                    or name.startswith(prefix + ".")
                    for name in sys.modules
                )
            )
            await registry.unload("demo")
            self.assertFalse(
                any(
                    name == prefix
                    or name.startswith(prefix + ".")
                    for name in sys.modules
                )
            )


class TestMCPManagerResiduals(unittest.IsolatedAsyncioTestCase):
    async def test_connect_is_idempotent(self):
        calls = {"connect": 0, "close": 0}

        class FakeTransport:
            def __init__(self):
                self.queue = asyncio.Queue()

            async def connect(self):
                calls["connect"] += 1

            async def send(self, message):
                method = message.get("method")
                if method == "initialize":
                    await self.queue.put(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": {
                                "capabilities": {"tools": {}},
                                "serverInfo": {},
                            },
                        }
                    )
                elif method == "tools/list":
                    await self.queue.put(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": {"tools": []},
                        }
                    )

            async def receive(self):
                return await self.queue.get()

            async def close(self):
                calls["close"] += 1

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MCPManager(temp_dir)
            config = MCPServerConfig(
                server_id="Demo",
                transport="inprocess",
                enabled=True,
            )
            transport = FakeTransport()
            manager.register_server(
                config, custom_transport=transport
            )
            first = await manager.connect("demo")
            second = await manager.connect("DEMO")
            self.assertIs(first, second)
            self.assertEqual(calls["connect"], 1)
            await manager.disconnect("demo")
            self.assertEqual(calls["close"], 1)

    async def test_adapter_uses_client_sync_bridge(self):
        class FakeClient:
            config = MCPServerConfig(
                server_id="demo",
                timeout_seconds=2,
            )

            def call_tool_sync(self, tool_name, arguments):
                return f"{tool_name}:{arguments['x']}"

        adapter = MCPToolAdapter(
            MCPTool(
                server_id="demo",
                name="echo",
            ),
            FakeClient(),
        )
        result = await asyncio.to_thread(
            adapter.execute, {"x": 7}
        )
        self.assertEqual(result, "echo:7")


class TestHTTPTransportResiduals(unittest.IsolatedAsyncioTestCase):
    async def test_sse_multiple_events_matches_requested_id(self):
        class Response:
            def __init__(self):
                self.lines = iter(
                    [
                        b'data: {"jsonrpc":"2.0","method":"note"}\n',
                        b"\n",
                        b'data: {"jsonrpc":"2.0","id":2,"result":{"x":2}}\n',
                        b"\n",
                        b'data: {"jsonrpc":"2.0","id":1,"result":{"x":1}}\n',
                        b"\n",
                    ]
                )

            def readline(self, _limit):
                return next(self.lines, b"")

        message = HTTPTransport._read_sse_message(
            Response(), 1
        )
        self.assertEqual(message["id"], 1)

    async def test_explicit_headers_preserved(self):
        transport = HTTPTransport(
            "https://example.com/mcp",
            headers={"Authorization": "Bearer token"},
        )
        transport._session_id = "session"
        headers = transport._request_headers()
        self.assertEqual(
            headers["Authorization"], "Bearer token"
        )
        self.assertEqual(
            headers["MCP-Session-Id"], "session"
        )
        self.assertEqual(
            headers["MCP-Protocol-Version"],
            "2024-11-05",
        )


class TestExtensionLifecycleResidual(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_error_is_not_silently_swallowed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = ExtensionManager(temp_dir)
            manager.state = manager.STATE_STARTED
            manager._started = True
            manager.mcp.disconnect_all = mock.AsyncMock(
                side_effect=RuntimeError("mcp close")
            )
            manager.plugins.stop_all = mock.AsyncMock()
            manager.hooks.run_observers = mock.AsyncMock()
            with self.assertRaises(RuntimeError):
                await manager.stop()
            self.assertEqual(
                manager.state,
                manager.STATE_STOPPED,
            )


if __name__ == "__main__":
    unittest.main()
