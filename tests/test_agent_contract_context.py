import unittest

import kitt.llm.agent_contract as contract
from kitt.llm.agent_contract import normalize_agent_route


class TestAgentContractContext(unittest.TestCase):
    def test_route_contract_is_closed_to_known_structural_routes(self):
        self.assertEqual(normalize_agent_route(None), "chat")
        self.assertEqual(normalize_agent_route("validate-diff"), "validate-diff")
        self.assertEqual(normalize_agent_route("agent-loop"), "agent-loop")
        with self.assertRaises(ValueError):
            normalize_agent_route("write-anything")

    def test_legacy_textual_context_helpers_are_not_exported(self):
        for name in (
            "split_workspace_context",
            "inject_agent_turn_context",
            "compact_reverse_proxy_orchestration",
            "infer_agent_route",
            "TURN_CONTEXT_MARKER",
            "TURN_CONTEXT_END_MARKER",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(contract, name))


if __name__ == "__main__":
    unittest.main()
