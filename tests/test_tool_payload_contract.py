import unittest

from kitt.llm.providers.kitt_reverse_proxy import prepare_reverse_proxy_system_prompt
from kitt.prompts import AGENT_EXECUTION_PERSONA


class ToolPayloadContractTests(unittest.TestCase):
    def test_execution_persona_requires_file_bodies_inside_mutation_tool_arguments(self):
        self.assertIn("FILE/CODE MUTATION PROTOCOL", AGENT_EXECUTION_PERSONA)
        self.assertIn("MUST be placed inside the arguments of an actual host mutation tool call", AGENT_EXECUTION_PERSONA)
        self.assertIn("never emitted as ordinary assistant text", AGENT_EXECUTION_PERSONA)
        self.assertIn("complete file content in its arguments", AGENT_EXECUTION_PERSONA)
        self.assertIn("only after the host has returned evidence", AGENT_EXECUTION_PERSONA)

    def test_reverse_proxy_native_tool_turn_keeps_payload_contract(self):
        prompt = """Answer directly and concisely.

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
        prepared, tools = prepare_reverse_proxy_system_prompt(prompt)

        self.assertTrue(tools)
        self.assertIsNotNone(prepared)
        self.assertIn("FILE/CODE MUTATION PROTOCOL", prepared)
        self.assertIn("complete file content in its arguments", prepared)


if __name__ == "__main__":
    unittest.main()
