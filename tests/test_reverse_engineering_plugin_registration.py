"""Ensure the opt-in plugin uses the existing MCP authority boundary."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from kitt.extensions.builtin_plugins.reverse_engineering import setup


class PluginRegistrationTests(unittest.IsolatedAsyncioTestCase):
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
            self.assertEqual(kwargs["allow_tools"], ["binary_inspect", "evidence_compare"])
            self.assertFalse("shell" in str(kwargs))
            await handle.stop()
            api.unregister_owned.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
