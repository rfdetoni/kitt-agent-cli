"""Tests for full extension subsystem lifecycle (ExtensionManager start/stop)."""
import asyncio
import tempfile
import textwrap
import unittest
from pathlib import Path

from kitt.extensions.manager import ExtensionManager
from kitt.extensions.mcp.models import MCPServerConfig
from kitt.extensions.mcp.transport import InProcessTransport
from kitt.tools.registry import ToolRegistry


class TestExtensionLifecycle(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.ws_plugins_dir = self.root / ".kitt" / "plugins"
        self.ws_plugins_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_extension_manager_start_and_stop_lifecycle(self):
        async def _test():
            # Setup sample plugin
            p_dir = self.ws_plugins_dir / "lifecycle-plugin"
            p_dir.mkdir(parents=True, exist_ok=True)
            (p_dir / "plugin.toml").write_text(
                textwrap.dedent("""
                name = "lifecycle-plugin"
                version = "1.0.0"
                api_version = "1"
                entrypoint = "plugin:setup"
                permissions = ["events.read"]
                """),
                encoding="utf-8",
            )
            (p_dir / "plugin.py").write_text(
                textwrap.dedent("""
                def setup(ctx):
                    events_seen = []
                    ctx.events.subscribe("test.event", lambda x: events_seen.append(x))
                    return None
                """),
                encoding="utf-8",
            )

            tool_reg = ToolRegistry(root_dir=str(self.root))
            mgr = ExtensionManager(workspace_root=str(self.root), tool_registry=tool_reg)

            # Start manager
            await mgr.start()

            # Verify plugin loaded
            p = mgr.plugins.get("lifecycle-plugin")
            self.assertIsNotNone(p)

            # Stop manager
            await mgr.stop()

            # Verify plugin stopped
            self.assertIsNone(mgr.plugins.get("lifecycle-plugin"))

        asyncio.run(_test())


if __name__ == "__main__":
    unittest.main()
