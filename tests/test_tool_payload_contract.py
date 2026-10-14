import unittest

from kitt.llm.domain import ProviderProtocolError
from kitt.llm.providers.kitt_reverse_proxy import (
    _reject_legacy_tool_contract,
    openai_tools_from_definitions,
)
from kitt.prompts import AGENT_EXECUTION_PERSONA
from kitt.tools.protocol import parse_tool_call


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

    def test_kap2_tool_dispatch_with_nested_host_arguments(self):
        frame = (
            "K2 T kitt_runtime\n"
            "operation: repo.read\n"
            "arguments:\n"
            "  path: README.md\n"
            "  start_line: 1\n"
            "  end_line: 100\n"
            "K2/END"
        )
        self.assertEqual(parse_tool_call(frame), (
            "kitt_runtime",
            {"operation": "repo.read", "arguments": {
                "path": "README.md", "start_line": 1, "end_line": 100,
            }},
        ))

    def test_kap2_invalid_and_final_envelopes_are_fail_closed(self):
        self.assertIsNone(parse_tool_call("K2 F\nstatus: INCOMPLETE\nK2/END"))
        self.assertIsNone(parse_tool_call("K2 W\nK2/END"))
        self.assertIsNone(parse_tool_call("K2 Q\nK2/END"))
        for frame in (
            "K2 T kitt_runtime operation: repo.read K2/END",
            "K2 T kitt_runtime\noperation: repo.list\nK2/END missing",
            "K2 T kitt_runtime\noperation: repo.list\noperation: process.run\nK2/END",
        ):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                parse_tool_call(frame)

    def test_textual_tool_contract_is_fail_closed(self):
        with self.assertRaises(ProviderProtocolError):
            _reject_legacy_tool_contract(
                "Tool Contract:\nAvailable host tools: [...]"
            )


if __name__ == "__main__":
    unittest.main()
