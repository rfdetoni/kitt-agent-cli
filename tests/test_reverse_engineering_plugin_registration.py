"""Ensure the opt-in plugin uses the existing MCP authority boundary."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from kitt.extensions.builtin_plugins.reverse_engineering import setup
from kitt.extensions.manifest import parse_manifest_file
import kitt.extensions.builtin_plugins.reverse_engineering as plugin_package


class PluginRegistrationTests(unittest.IsolatedAsyncioTestCase):
    def test_manifest_is_compatible_and_opt_in(self):
        manifest_path = Path(plugin_package.__file__).with_name('plugin.toml')
        manifest = parse_manifest_file(manifest_path, source='bundled')
        self.assertEqual(manifest.name, 'kitt-reverse-engineering')
        self.assertFalse(manifest.enabled_by_default)
        self.assertEqual(manifest.permissions, {'mcp.manage'})

    async def test_register_stdio_and_unregister_owned(self):
        with tempfile.TemporaryDirectory() as root:
            api = Mock()
            api.unregister_owned = unittest.mock.AsyncMock()
            ctx = Mock()
            ctx.workspace_root = Path(root)
            ctx.mcp = api
            handle = setup(ctx)
            await handle.start()
            api.register_stdio.assert_called_once()
            args, kwargs = api.register_stdio.call_args
            self.assertEqual(args[0], "reverse-engineering")
            self.assertEqual(kwargs["trust"], "restricted")
            self.assertEqual(kwargs["allow_tools"], ["binary_inspect", "evidence_compare", "evidence_probe_compare", "evidence_query", "lab_mission_plan"])
            self.assertFalse("shell" in str(kwargs))
            await handle.stop()
            api.unregister_owned.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
