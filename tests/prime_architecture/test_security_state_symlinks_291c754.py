from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from kitt.extensions.errors import MCPError, PluginLoadError
from kitt.extensions.mcp.security import MCPTrustStore
from kitt.extensions.plugins.security import PluginStateStore, PluginTrustStore


@unittest.skipIf(os.name == "nt", "POSIX symlink/permission semantics")
class TestSecurityStateSymlinks(unittest.TestCase):
    @staticmethod
    def _symlink(path: Path) -> None:
        target = path.parent.parent / f"{path.name}.target"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            '{"version": 1, "workspaces": {}}',
            encoding="utf-8",
        )
        os.chmod(target, 0o600)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)

    def test_mcp_trust_store_rejects_final_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            link = root / "state" / "mcp-trust.json"
            self._symlink(link)
            store = MCPTrustStore(root, path=link)
            self.assertEqual(store.path, link.absolute())
            with self.assertRaises(MCPError):
                store._data()

    def test_plugin_trust_store_rejects_final_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            link = root / "state" / "plugin-trust.json"
            self._symlink(link)
            store = PluginTrustStore(root, path=link)
            self.assertEqual(store.path, link.absolute())
            with self.assertRaises(PluginLoadError):
                store._data()

    def test_plugin_state_store_rejects_final_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            link = root / "state" / "plugin-state.json"
            self._symlink(link)
            store = PluginStateStore(root, path=link)
            self.assertEqual(store.path, link.absolute())
            with self.assertRaises(PluginLoadError):
                store._data()

    def test_mcp_trust_store_rejects_world_readable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "mcp-trust.json"
            path.write_text(
                '{"version": 1, "workspaces": {}}',
                encoding="utf-8",
            )
            os.chmod(path, 0o644)
            store = MCPTrustStore(root, path=path)
            with self.assertRaises(MCPError):
                store._data()

    def test_plugin_trust_store_rejects_world_readable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "plugin-trust.json"
            path.write_text(
                '{"version": 1, "workspaces": {}}',
                encoding="utf-8",
            )
            os.chmod(path, 0o644)
            store = PluginTrustStore(root, path=path)
            with self.assertRaises(PluginLoadError):
                store._data()

    def test_plugin_state_store_rejects_world_readable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "plugin-state.json"
            path.write_text(
                '{"version": 1, "workspaces": {}}',
                encoding="utf-8",
            )
            os.chmod(path, 0o644)
            store = PluginStateStore(root, path=path)
            with self.assertRaises(PluginLoadError):
                store._data()


if __name__ == "__main__":
    unittest.main()
