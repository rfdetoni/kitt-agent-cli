import unittest

from kitt.llm.agent_contract import normalize_agent_route


class AgentRoutePinRegressionTests(unittest.TestCase):
    def test_agent_loop_route_is_structural_and_stable_across_follow_ups(self):
        for route in ("agent-loop", "code-generation", "code-edit", "validate-diff"):
            with self.subTest(route=route):
                self.assertEqual(normalize_agent_route(route), route)

    def test_natural_language_cannot_create_a_route_name(self):
        for value in (
            "Create backend and frontend",
            "Crie o projeto",
            "任意の言語で要求を書けます。",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_agent_route(value)


if __name__ == "__main__":
    unittest.main()
