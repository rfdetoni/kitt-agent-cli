from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from kitt.daemon.protocol import decode_line
from kitt.extensions.errors import MCPError
from kitt.extensions.mcp.manager import MCPManager
from kitt.extensions.mcp.security import MCPTrustStore


class TestWorkspaceMCPTrust(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _write(root: Path, command: str = "python") -> None:
        kitt = root / ".kitt"
        kitt.mkdir(parents=True, exist_ok=True)
        (kitt / "mcp.json").write_text(
            json.dumps(
                {
                    "mcp": {
                        "repo_server": {
                            "transport": "stdio",
                            "command": command,
                            "args": ["-c", "print('x')"],
                            "enabled": True,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _trust_store(root: Path) -> MCPTrustStore:
        return MCPTrustStore(
            root,
            path=root / ".tmp-security" / "mcp-trust.json",
        )

    async def test_workspace_server_is_untrusted_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root)
            manager = MCPManager(root)
            self.assertFalse(manager.is_trusted("repo_server"))
            await manager.connect_all_enabled()
            self.assertEqual(
                manager.get_server_status("repo_server").value,
                "DISCONNECTED",
            )

    async def test_explicit_connect_is_blocked_before_trust(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root)
            manager = MCPManager(root)
            with self.assertRaises(MCPError):
                await manager.connect("repo_server")

    async def test_config_change_invalidates_trust(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "python")
            first = MCPManager(root, trust_store=self._trust_store(root))
            first.trust_server("repo_server")
            self.assertTrue(first.is_trusted("repo_server"))
            self._write(root, "python3")
            second = MCPManager(root, trust_store=first.trust_store)
            self.assertFalse(second.is_trusted("repo_server"))

    async def test_untrust_revokes_exact_workspace_grant(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root)
            manager = MCPManager(root, trust_store=self._trust_store(root))
            manager.trust_server("repo_server")
            self.assertTrue(manager.is_trusted("repo_server"))
            removed = await manager.untrust_server("repo_server")
            self.assertTrue(removed)
            self.assertFalse(manager.is_trusted("repo_server"))

    async def test_untrust_still_revokes_after_config_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root)
            trust_store = self._trust_store(root)
            first = MCPManager(root, trust_store=trust_store)
            first.trust_server("repo_server")
            (root / ".kitt" / "mcp.json").unlink()
            second = MCPManager(root, trust_store=trust_store)
            removed = await second.untrust_server("repo_server")
            self.assertTrue(removed)

    def test_symlink_workspace_config_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kitt = root / ".kitt"
            kitt.mkdir(parents=True, exist_ok=True)
            target = root / "actual-mcp.json"
            target.write_text('{"mcp": {}}', encoding="utf-8")
            link = kitt / "mcp.json"
            try:
                link.symlink_to(target)
            except (NotImplementedError, OSError):
                self.skipTest("symlink creation not available")
            manager = MCPManager(root)
            self.assertEqual(manager.list_servers(), [])

    def test_oversized_workspace_config_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kitt = root / ".kitt"
            kitt.mkdir(parents=True, exist_ok=True)
            payload = " " * ((1024 * 1024) + 1)
            (kitt / "mcp.json").write_text(payload, encoding="utf-8")
            manager = MCPManager(root)
            self.assertEqual(manager.list_servers(), [])


class TestDaemonWorkspaceBoundary(unittest.TestCase):
    def test_workspace_boundary(self):
        from kitt.daemon.server import DaemonServer
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = DaemonServer(root)
            self.assertTrue(server._workspace_allowed(root))
            self.assertFalse(server._workspace_allowed(root.parent))

    def test_cross_workspace_ipc_is_blocked_before_runtime_lookup(self):
        from kitt.daemon.server import DaemonServer

        class FakeReader:
            def __init__(self, lines: list[bytes]):
                self._lines = list(lines)

            async def readline(self):
                await asyncio.sleep(0.01)
                if self._lines:
                    return self._lines.pop(0)
                return b""

        class FakeWriter:
            def __init__(self):
                self.messages: list[bytes] = []
                self.closed = False

            def write(self, data: bytes):
                self.messages.append(data)

            async def drain(self):
                return None

            def close(self):
                self.closed = True

            async def wait_closed(self):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = DaemonServer(root)
            server.token = "secret"
            server._running = True

            async def _unexpected_runtime(*_args, **_kwargs):
                raise AssertionError("runtime lookup should stay pinned and blocked")

            server._get_or_create_runtime = _unexpected_runtime
            foreign = root.parent / "foreign"
            reader = FakeReader(
                [
                    (
                        json.dumps(
                            {"action": "auth", "token": "secret"}
                        )
                        + "\n"
                    ).encode("utf-8"),
                    (
                        json.dumps(
                            {
                                "action": "list_sessions",
                                "workspace": str(foreign),
                            }
                        )
                        + "\n"
                    ).encode("utf-8"),
                ]
            )
            writer = FakeWriter()

            asyncio.run(server._handle_client(reader, writer))

            responses = [decode_line(msg) for msg in writer.messages]
            self.assertEqual(responses[0]["status"], "ok")
            self.assertEqual(responses[1]["status"], "error")
            self.assertEqual(
                responses[1]["error"],
                "Cross-workspace daemon request blocked",
            )
            self.assertTrue(writer.closed)


if __name__ == "__main__":
    unittest.main()
