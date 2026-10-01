import unittest

from kitt.llm.agent_contract import normalize_agent_route


class ReverseProxyAgentLoopRoutingTests(unittest.TestCase):
    def test_mutation_routes_are_closed_structural_values(self):
        self.assertEqual(normalize_agent_route("agent-loop"), "agent-loop")
        self.assertEqual(normalize_agent_route("code-generation"), "code-generation")
        self.assertEqual(normalize_agent_route("code-edit"), "code-edit")

    def test_prompt_text_is_not_a_route_fallback(self):
        for request in (
            "Não crie backend; implemente somente o frontend solicitado.",
            "Do not create a backend; implement only the requested frontend.",
            "バックエンドを作らず、依頼された範囲だけ実装してください。",
        ):
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    normalize_agent_route(request)


if __name__ == "__main__":
    unittest.main()
