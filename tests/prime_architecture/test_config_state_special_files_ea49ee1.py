from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from kitt.extensions.errors import MCPError, PluginLoadError
from kitt.extensions.mcp.manager import MCPManager
from kitt.extensions.mcp.security import MCPTrustStore
from kitt.extensions.plugins.security import PluginStateStore, PluginTrustStore


@unittest.skipIf(os.name == "nt", "POSIX file security semantics")
class TestTrustedConfigPath(unittest.TestCase):
    def test_global_mcp_symlink_is_not_resolved_away(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "actual.json"
            target.write_text(
                json.dumps(
                    {
                        "mcp": {
                            "evil": {
                                "transport": "stdio",
                                "command": "python",
                                "enabled": True,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            os.chmod(target, 0o600)

            link = root / "global-mcp.json"
            link.symlink_to(target)

            manager = MCPManager(
                root / "workspace",
                config_file=str(link),
            )
            self.assertEqual(manager.list_servers(), [])

    def test_global_mcp_world_readable_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "global-mcp.json"
            config.write_text(
                json.dumps(
                    {
                        "mcp": {
                            "server": {
                                "transport": "http",
                                "url": "https://example.com/mcp",
                                "enabled": False,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            os.chmod(config, 0o644)

            manager = MCPManager(
                root / "workspace",
                config_file=str(config),
            )
            self.assertEqual(manager.list_servers(), [])

    def test_global_mcp_fifo_is_rejected_without_blocking(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fifo = root / "mcp.fifo"
            os.mkfifo(fifo, 0o600)

            manager = MCPManager(
                root / "workspace",
                config_file=str(fifo),
            )
            self.assertEqual(manager.list_servers(), [])


@unittest.skipIf(os.name == "nt", "POSIX special-file semantics")
class TestStateFilesAreRegular(unittest.TestCase):
    def test_mcp_trust_fifo_is_rejected(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fifo = root / "mcp-trust.fifo"
            os.mkfifo(fifo, 0o600)
            store = MCPTrustStore(root, path=fifo)
            with self.assertRaises(MCPError):
                store._data()

    def test_plugin_trust_fifo_is_rejected(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fifo = root / "plugin-trust.fifo"
            os.mkfifo(fifo, 0o600)
            store = PluginTrustStore(root, path=fifo)
            with self.assertRaises(PluginLoadError):
                store._data()

    def test_plugin_state_fifo_is_rejected(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fifo = root / "plugin-state.fifo"
            os.mkfifo(fifo, 0o600)
            store = PluginStateStore(root, path=fifo)
            with self.assertRaises(PluginLoadError):
                store._data()


if __name__ == "__main__":
    unittest.main()
