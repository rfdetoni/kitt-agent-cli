import unittest

from kitt.prompts import (
    AGENT_EXECUTION_PERSONA,
    CONCISE_PERSONA,
    CONTEXT_SUMMARY_SYSTEM,
    normalize_execution_system_prompt,
)


class TestPromptIdentityNeutrality(unittest.TestCase):
    def test_tool_contract_activates_agent_mode_without_product_name(self):
        source = """Answer directly and concisely.

Tool Contract:
Available host tool: [{'name': 'kitt_runtime', 'args': {'operation': 'string'}}]

Memory:
none
"""
        normalized = normalize_execution_system_prompt(source)

        self.assertTrue(normalized.startswith(AGENT_EXECUTION_PERSONA))
        self.assertIn("Tool Contract:", normalized)
        self.assertNotIn("K.I.T.T.", normalized)

    def test_context_summary_cannot_be_promoted_by_appended_tool_contract(self):
        source = (
            f"{CONTEXT_SUMMARY_SYSTEM}\n\n"
            "Tool Contract:\nAvailable host tool: [{'name': 'kitt_runtime'}]\n\n"
            "Memory:\nexecution context"
        )

        normalized = normalize_execution_system_prompt(source)

        self.assertEqual(normalized, CONTEXT_SUMMARY_SYSTEM)
        self.assertNotIn("Tool Contract:", normalized)
        self.assertNotIn(AGENT_EXECUTION_PERSONA, normalized)
        self.assertIn("do not use or request host tools", normalized)

    def test_legacy_name_addressed_prompt_does_not_change_plain_chat_mode(self):
        source = (
            "You are K.I.T.T., the autonomous coding agent. "
            "Answer in one direct, concise sentence. Do not expose reasoning."
        )
        normalized = normalize_execution_system_prompt(source)

        self.assertEqual(normalized, CONCISE_PERSONA)
        self.assertNotIn("K.I.T.T.", normalized)

    def test_unrelated_prompt_is_unchanged(self):
        source = "Return valid JSON only."
        self.assertEqual(normalize_execution_system_prompt(source), source)


if __name__ == "__main__":
    unittest.main()
