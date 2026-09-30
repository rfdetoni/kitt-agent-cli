import unittest

from kitt.llm.domain import ProviderProtocolError
from kitt.llm.providers.kitt_reverse_proxy import (
    _reject_legacy_tool_contract,
    openai_tools_from_definitions,
)
from kitt.prompts import AGENT_EXECUTION_PERSONA


class ToolPayloadContractTests(unittest.TestCase):
    def test_execution_persona_requires_file_bodies_inside_mutation_tool_arguments(self):
        self.assertIn("FILE/CODE MUTATION PROTOCOL", AGENT_EXECUTION_PERSONA)
        self.assertIn(
            "MUST be placed inside the arguments of an actual host mutation tool call",
            AGENT_EXECUTION_PERSONA,
        )
        self.assertIn("never emitted as ordinary assistant text", AGENT_EXECUTION_PERSONA)
        self.assertIn("complete file content in its arguments", AGENT_EXECUTION_PERSONA)
        self.assertIn("only after the host has returned evidence", AGENT_EXECUTION_PERSONA)

    def test_reverse_proxy_tool_turn_uses_structural_schema_only(self):
        tools = openai_tools_from_definitions([
            {
                "name": "kitt_runtime",
                "description": "Workspace runtime",
                "args": {
                    "operation": {
                        "type": "string",
                        "enum": ["repo.read", "repo.write_file"],
                    },
                    "arguments": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
            }
        ])

        self.assertEqual(tools[0]["function"]["name"], "kitt_runtime")
        self.assertEqual(
            tools[0]["function"]["parameters"]["properties"]["operation"]["enum"],
            ["repo.read", "repo.write_file"],
        )

    def test_textual_tool_contract_is_fail_closed(self):
        with self.assertRaises(ProviderProtocolError):
            _reject_legacy_tool_contract(
                "Tool Contract:\nAvailable host tools: [...]"
            )


if __name__ == "__main__":
    unittest.main()
