import unittest

from kitt.llm.domain import ProviderProtocolError
from kitt.llm.providers.kitt_reverse_proxy import (
    _TOOL_RETRY_PROMPT,
    _reject_legacy_tool_contract,
    openai_tools_from_definitions,
)


class TestReverseProxyAgentPrompt(unittest.TestCase):
    def test_structural_tool_catalog_is_promoted_to_native_agent_tools(self):
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
        self.assertEqual([tool["function"]["name"] for tool in tools], ["kitt_runtime"])
        schema = tools[0]["function"]["parameters"]
        self.assertEqual(
            schema["properties"]["operation"]["enum"],
            ["repo.read", "repo.write_file"],
        )

    def test_legacy_textual_tool_contract_is_rejected(self):
        with self.assertRaises(ProviderProtocolError):
            _reject_legacy_tool_contract(
                "Tool Contract:\nAvailable host tools: [...]"
            )

    def test_internal_reverse_proxy_retry_prompt_is_english(self):
        self.assertIn("The request is not complete yet.", _TOOL_RETRY_PROMPT)
        self.assertIn("execute the requested mutation", _TOOL_RETRY_PROMPT)
        for portuguese in (
            "solicitação",
            "Emita agora",
            "mutação solicitada",
            "Não responda",
        ):
            self.assertNotIn(portuguese, _TOOL_RETRY_PROMPT)

    def test_plain_chat_without_tool_contract_is_unchanged(self):
        _reject_legacy_tool_contract(
            "Answer in one direct, concise sentence. Do not expose reasoning."
        )


if __name__ == "__main__":
    unittest.main()
