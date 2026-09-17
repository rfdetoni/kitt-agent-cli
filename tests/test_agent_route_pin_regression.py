import unittest

from kitt.llm.agent_contract import infer_agent_route


SYSTEM_PROMPT = '''Tool Contract:
{"name":"kitt_runtime"}
{"name":"git_status"}
{"name":"git_diff"}

Memory:
'''


class AgentRoutePinRegressionTests(unittest.TestCase):
    def test_implement_route_survives_validation_follow_up(self):
        messages = [
            {"role": "user", "content": "Intent: IMPLEMENT\n\nGoal:\nCreate backend and frontend."},
            {"role": "assistant", "content": "tool call"},
            {"role": "user", "content": "Validate the diff and report status."},
        ]
        self.assertEqual(infer_agent_route(SYSTEM_PROMPT, messages), "code-generation")

    def test_literal_creation_route_survives_completion_guard(self):
        messages = [
            {"role": "user", "content": "Crie o projeto MeuFazTudo com backend e frontend Angular."},
            {"role": "user", "content": "[KITT COMPLETION VERIFICATION]\nCheck files now."},
        ]
        self.assertEqual(infer_agent_route(SYSTEM_PROMPT, messages), "code-generation")

    def test_refactor_route_survives_read_only_follow_up(self):
        messages = [
            {"role": "user", "content": "Intent: REFACTOR\n\nGoal:\nRefactor backend code."},
            {"role": "user", "content": "Inspect git diff only."},
        ]
        self.assertEqual(infer_agent_route(SYSTEM_PROMPT, messages), "code-edit")


if __name__ == "__main__":
    unittest.main()
