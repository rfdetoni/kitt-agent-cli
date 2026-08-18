"""Tests for dynamic tool integration in ToolRegistry from plugins and MCP."""
import unittest

from kitt.tools.registry import ToolRegistry


class TestExtensionTools(unittest.TestCase):

    def test_dynamic_tool_registration_and_execution(self):
        reg = ToolRegistry()

        def my_custom_tool(args):
            name = args.get("name", "World")
            return f"Hello, {name}!"

        reg.register(
            "custom_greet",
            my_custom_tool,
            description="Greet someone",
            schema={"name": "string"},
            owner_plugin_id="plugin:greeter",
        )

        # 1. Verify tool appears in definitions
        defs = reg.get_tool_definitions()
        self.assertTrue(any(t["name"] == "custom_greet" for t in defs))

        # 2. Execute tool
        result = reg.execute_tool("custom_greet", {"name": "Alice"})
        self.assertTrue(result.success)
        self.assertEqual(result.output, "Hello, Alice!")

        # 3. Unload by owner
        removed = reg.unregister_by_owner("plugin:greeter")
        self.assertEqual(removed, 1)

        # 4. Verify tool is no longer available
        res_after = reg.execute_tool("custom_greet", {"name": "Alice"})
        self.assertFalse(res_after.success)


if __name__ == "__main__":
    unittest.main()
