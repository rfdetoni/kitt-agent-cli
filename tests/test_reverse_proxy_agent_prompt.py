import unittest

from kitt.llm.providers.kitt_reverse_proxy import (
    extract_openai_tools,
    prepare_reverse_proxy_system_prompt,
)
from kitt.prompts import KITT_AGENT_PERSONA


class TestReverseProxyAgentPrompt(unittest.TestCase):
    def test_multiline_tool_catalog_is_promoted_to_native_agent_tools(self):
        system_prompt = """Answer directly and concisely.

Tool Contract:
Available host tools: [
  {
    'name': 'kitt_runtime',
    'description': 'Workspace runtime',
    'args': {
      'operation': {'type': 'string', 'enum': ['repo.read', 'repo.write_file']},
      'arguments': {'type': 'object', 'additionalProperties': True}
    }
  }
]
For a host tool, respond with exactly: <kitt-tool>...</kitt-tool>

Memory:
none
"""
        tools = extract_openai_tools(system_prompt)
        self.assertEqual([tool["function"]["name"] for tool in tools], ["kitt_runtime"])

        prepared, prepared_tools = prepare_reverse_proxy_system_prompt(system_prompt)
        self.assertEqual(prepared_tools, tools)
        self.assertIn(KITT_AGENT_PERSONA, prepared)
        self.assertNotIn("Answer directly and concisely.", prepared)
        self.assertNotIn("Tool Contract:", prepared)
        self.assertIn("Memory:\nnone", prepared)

    def test_failed_native_tool_extraction_preserves_legacy_execution_path(self):
        system_prompt = """Answer in one direct, concise sentence. Do not expose reasoning.

Tool Contract:
Available host tools: [
  {'name': 'kitt_runtime', 'args': {'operation': 'string'}}
For a host tool, respond with exactly: <kitt-tool>...</kitt-tool>

Memory:
none
"""
        prepared, tools = prepare_reverse_proxy_system_prompt(system_prompt)

        self.assertEqual(tools, [])
        self.assertIn(KITT_AGENT_PERSONA, prepared)
        self.assertNotIn(
            "Answer in one direct, concise sentence. Do not expose reasoning.",
            prepared,
        )
        self.assertIn("Tool Contract:", prepared)
        self.assertIn("<kitt-tool>", prepared)

    def test_plain_chat_without_tool_contract_keeps_non_agent_system_prompt(self):
        system_prompt = "Answer in one direct, concise sentence. Do not expose reasoning."
        prepared, tools = prepare_reverse_proxy_system_prompt(system_prompt)

        self.assertEqual(tools, [])
        self.assertEqual(prepared, system_prompt)
        self.assertNotIn(KITT_AGENT_PERSONA, prepared)


if __name__ == "__main__":
    unittest.main()
