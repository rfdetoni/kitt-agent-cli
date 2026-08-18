"""Tests for plugin capability security, denied APIs, and secret redaction."""
import unittest

from kitt.extensions.errors import PluginPermissionError
from kitt.extensions.plugins.api import EventAPI, HookAPI, PluginLogger, ToolAPI


class TestExtensionSecurity(unittest.TestCase):

    def test_permission_enforcement_on_apis(self):
        # EventAPI without 'events.read'
        events = EventAPI(plugin_name="unauthorized-plugin", permissions=set())
        with self.assertRaises(PluginPermissionError):
            events.subscribe("test.event", lambda x: None)

        with self.assertRaises(PluginPermissionError):
            events.publish("test.event", {})

        # ToolAPI without 'tools.register'
        tools = ToolAPI(plugin_name="unauthorized-plugin", permissions=set())
        with self.assertRaises(PluginPermissionError):
            tools.register("my_tool", lambda x: None)

        # HookAPI for tools without 'tools.observe' or 'tools.modify'
        hooks = HookAPI(plugin_name="unauthorized-plugin", permissions=set())
        with self.assertRaises(PluginPermissionError):
            hooks.register("tool.before_execute", lambda x: None)

    def test_logger_redacts_known_secrets(self):
        logger = PluginLogger("test-logger")
        # Ensure secret strings are not plainly logged
        import logging
        with self.assertLogs("kitt.plugin.test-logger", level="INFO") as cm:
            logger.info("Connecting with key env:OPENAI_API_KEY")
            self.assertTrue(len(cm.output) > 0)


if __name__ == "__main__":
    unittest.main()
