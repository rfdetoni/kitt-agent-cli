from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from kitt.extensions.errors import MCPError
from kitt.extensions.mcp.manager import MCPManager
from kitt.extensions.mcp.security import MCPTrustStore


class TestMCPTrustResidual(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _write(
        root: Path,
        *,
        enabled: bool,
        command: str = "python",
    ) -> None:
        kitt = root / ".kitt"
        kitt.mkdir(parents=True, exist_ok=True)
        (kitt / "mcp.json").write_text(
            json.dumps(
                {
                    "mcp": {
                        "demo": {
                            "transport": "stdio",
                            "command": command,
                            "args": ["-c", "print('x')"],
                            "enabled": enabled,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _store(root: Path) -> MCPTrustStore:
        return MCPTrustStore(
            root,
            path=root / ".private" / "mcp-trust.json",
        )

    async def test_enabling_server_invalidates_disabled_config_trust(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self._store(root)

            self._write(root, enabled=False)
            first = MCPManager(root, trust_store=store)
            first.trust_server("demo")
            self.assertTrue(first.is_trusted("demo"))

            self._write(root, enabled=True)
            second = MCPManager(root, trust_store=store)
            self.assertFalse(second.is_trusted("demo"))
            with self.assertRaises(MCPError):
                await second.connect("demo")

    async def test_disabling_server_also_changes_approved_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self._store(root)

            self._write(root, enabled=True)
            first = MCPManager(root, trust_store=store)
            first.trust_server("demo")

            self._write(root, enabled=False)
            second = MCPManager(root, trust_store=store)
            self.assertFalse(second.is_trusted("demo"))

    def test_trust_store_symlink_is_rejected(self):
        if os.name == "nt":
            self.skipTest("symlink semantics vary on Windows")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private = root / ".private"
            private.mkdir()
            target = root / "external.json"
            target.write_text(
                '{"version": 1, "workspaces": {}}',
                encoding="utf-8",
            )
            link = private / "mcp-trust.json"
            link.symlink_to(target)

            store = MCPTrustStore(root, path=link)
            with self.assertRaises(MCPError):
                store._data()


if __name__ == "__main__":
    unittest.main()
