from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from kitt.extensions.mcp.manager import MCPManager


class TestMCPConfigParserHardening(unittest.TestCase):
    @staticmethod
    def _write_workspace(root: Path, payload: dict) -> None:
        config_dir = root / ".kitt"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "mcp.json").write_text(
            json.dumps({"mcp": payload}),
            encoding="utf-8",
        )

    @staticmethod
    def _write_global(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"mcp": payload}),
            encoding="utf-8",
        )
        if os.name != "nt":
            os.chmod(path, 0o600)

    def test_malformed_workspace_server_does_not_crash_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workspace(
                root,
                {
                    "bad": {
                        "transport": "stdio",
                        "command": "python",
                        "deny_tools": None,
                    }
                },
            )
            manager = MCPManager(root)
            self.assertEqual(manager.list_servers(), [])

    def test_non_boolean_enabled_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workspace(
                root,
                {
                    "bad": {
                        "transport": "stdio",
                        "command": "python",
                        "enabled": "false",
                    }
                },
            )
            manager = MCPManager(root)
            self.assertEqual(manager.list_servers(), [])

    def test_nan_timeout_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / ".kitt"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "mcp.json").write_text(
                '{"mcp":{"bad":{"transport":"stdio","command":"python","timeout_seconds":NaN}}}',
                encoding="utf-8",
            )
            manager = MCPManager(root)
            self.assertEqual(manager.list_servers(), [])

    def test_negative_output_limit_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_workspace(
                root,
                {
                    "bad": {
                        "transport": "stdio",
                        "command": "python",
                        "max_output_bytes": -1,
                    }
                },
            )
            manager = MCPManager(root)
            self.assertEqual(manager.list_servers(), [])

    def test_invalid_workspace_override_does_not_remove_global_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workspace"
            root.mkdir()
            global_path = base / "mcp-global.json"
            self._write_global(
                global_path,
                {
                    "shared": {
                        "transport": "http",
                        "url": "https://example.com/mcp",
                        "enabled": False,
                    }
                },
            )
            self._write_workspace(
                root,
                {
                    "shared": {
                        "transport": "stdio",
                        "command": "python",
                        "deny_tools": None,
                    }
                },
            )
            manager = MCPManager(root, config_file=str(global_path))
            config = manager.get_config("shared")
            self.assertEqual(config.source, "global")
            self.assertEqual(config.transport, "http")

    def test_valid_workspace_server_cannot_shadow_global_server_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workspace"
            root.mkdir()
            global_path = base / "mcp-global.json"
            self._write_global(
                global_path,
                {
                    "shared": {
                        "transport": "http",
                        "url": "https://example.com/mcp",
                        "enabled": False,
                    }
                },
            )
            self._write_workspace(
                root,
                {
                    "shared": {
                        "transport": "stdio",
                        "command": "python",
                        "enabled": False,
                    }
                },
            )
            manager = MCPManager(root, config_file=str(global_path))
            config = manager.get_config("shared")
            self.assertEqual(config.source, "global")
            self.assertEqual(config.transport, "http")


if __name__ == "__main__":
    unittest.main()
