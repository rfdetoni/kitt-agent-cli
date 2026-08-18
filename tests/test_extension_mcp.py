"""Tests for Model Context Protocol (MCP) in-process client, tools, resources, and manager."""
import asyncio
import json
import unittest

from kitt.extensions.mcp.client import MCPClient
from kitt.extensions.mcp.manager import MCPManager
from kitt.extensions.mcp.models import MCPServerConfig
from kitt.extensions.mcp.transport import InProcessTransport
from kitt.tools.registry import ToolRegistry


class TestExtensionMCP(unittest.TestCase):

    def setUp(self):
        # Mock in-process MCP server handler
        def mock_server(msg):
            req_id = msg.get("id")
            method = msg.get("method")
            if method == "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "MockServer", "version": "1.0"},
                        "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                    },
                }
            elif method == "tools/list":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "Echo back input text",
                                "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                            }
                        ]
                    },
                }
            elif method == "tools/call":
                args = msg.get("params", {}).get("arguments", {})
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": f"ECHO: {args.get('text', '')}"}]},
                }
            elif method == "resources/list":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"resources": [{"uri": "mock://test-resource", "name": "Test Resource"}]},
                }
            elif method == "resources/read":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"contents": [{"text": "Resource content data"}]},
                }
            return None

        self.mock_server_fn = mock_server

    def test_mcp_client_handshake_and_tool_call(self):
        async def _test():
            transport = InProcessTransport(self.mock_server_fn)
            config = MCPServerConfig(server_id="mock-srv")
            client = MCPClient(config, transport)

            # 1. Connect
            await client.connect()

            # 2. List tools
            tools = await client.list_tools()
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0].name, "echo")
            self.assertEqual(tools[0].full_name, "mcp.mock-srv.echo")

            # 3. Call tool
            res = await client.call_tool("echo", {"text": "hello mcp"})
            self.assertEqual(res, "ECHO: hello mcp")

            # 4. Resources
            resources = await client.list_resources()
            self.assertEqual(len(resources), 1)
            res_content = await client.read_resource("mock://test-resource")
            self.assertEqual(res_content, "Resource content data")

            # 5. Disconnect
            await client.disconnect()

        asyncio.run(_test())

    def test_mcp_manager_registers_tools_in_tool_registry(self):
        async def _test():
            tool_reg = ToolRegistry()
            mcp_mgr = MCPManager(tool_registry=tool_reg)

            transport = InProcessTransport(self.mock_server_fn)
            config = MCPServerConfig(server_id="demo-srv")
            mcp_mgr.register_server(config, custom_transport=transport)

            # Connect
            await mcp_mgr.connect("demo-srv", transport=transport)

            # Verify tool is available in tool_reg
            tool_defs = tool_reg.get_tool_definitions()
            self.assertTrue(any(t["name"] == "mcp.demo-srv.echo" for t in tool_defs))

            # Disconnect
            await mcp_mgr.disconnect("demo-srv")

            # Tool should be removed on disconnect
            tool_defs_after = tool_reg.get_tool_definitions()
            self.assertFalse(any(t["name"] == "mcp.demo-srv.echo" for t in tool_defs_after))

        asyncio.run(_test())


if __name__ == "__main__":
    unittest.main()
