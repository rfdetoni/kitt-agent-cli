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

    def test_json_tool_dispatch_with_nested_host_arguments(self):
        frame = (
            '{"action":"use_tool","tool":"kitt_runtime","tool_input":{"operation":"repo.read","arguments":{"path":"README.md","start_line":1,"end_line":100}},"content":null,"reasoning_summary":"","loop":null}'
        )
        self.assertEqual(parse_tool_call(frame), (
            "kitt_runtime",
            {"operation": "repo.read", "arguments": {
                "path": "README.md", "start_line": 1, "end_line": 100,
            }},
        ))

    def test_json_invalid_and_final_envelopes_are_fail_closed(self):
        self.assertIsNone(parse_tool_call('{"action":"final_response","tool":null,"tool_input":null,"content":{"status":"INCOMPLETE"},"reasoning_summary":"","loop":null}'))
        self.assertIsNone(parse_tool_call('{"action":"request_workspace","tool":null,"tool_input":null,"content":null,"reasoning_summary":"","loop":null}'))
        self.assertIsNone(parse_tool_call('{"action":"request_tools","tool":null,"tool_input":null,"content":null,"reasoning_summary":"","loop":null}'))
        for frame in (
            '{"action":"use_tool","tool":"kitt_runtime","tool_input":',
            '{"action":"use_tool","tool":"kitt_runtime","tool_input":null}',
            '{"action":"use_tool","tool":"kitt_runtime","tool_input":{"operation":"repo.list","operation":"process.run"}}',
        ):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                parse_tool_call(frame)

    def test_agent_loop_executes_only_explicit_complete_host_actions(self):
        compact = '<tool_call>{"name":"kitt_runtime","arguments":{"operation":"repo.read","arguments":{"path":"README.md"}}}</tool_call>'
        native = '<kitt-tool>{"id":"call_1","name":"kitt_runtime","arguments":{"operation":"repo.read","arguments":{"path":"README.md"}}}</kitt-tool>'
        expected = ("kitt_runtime", {"operation": "repo.read", "arguments": {"path": "README.md"}})
        self.assertEqual(parse_tool_call(compact, explicit_only=True), expected)
        self.assertEqual(parse_tool_call(native, explicit_only=True), expected)
        for candidate in (
            "Here is an example:\n" + compact,
            compact + "\n" + compact,
            compact[:-12],
            'run_command("echo hello")',
            '```python\nread_file("secret.txt")\n```',
            '<tool_call>{"name":"kitt_runtime","arguments":{"operation":"repo.read","operation":"process.run"}}</tool_call>',
        ):
            with self.subTest(candidate=candidate):
                try:
                    parsed = parse_tool_call(candidate, explicit_only=True)
                except ValueError:
                    continue
                self.assertIsNone(parsed, "prose and malformed frames are not tool authority")

    def test_textual_tool_contract_is_fail_closed(self):
        with self.assertRaises(ProviderProtocolError):
            _reject_legacy_tool_contract(
                "Tool Contract:\nAvailable host tools: [...]"
            )


if __name__ == "__main__":
    unittest.main()
