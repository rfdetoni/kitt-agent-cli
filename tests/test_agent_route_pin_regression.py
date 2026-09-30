import unittest

from kitt.llm.agent_contract import infer_agent_route


SYSTEM_PROMPT = '''Tool Contract:
{"name":"kitt_runtime"}
{"name":"git_status"}
{"name":"git_diff"}

Memory:
'''


class AgentRoutePinRegressionTests(unittest.TestCase):
    def test_tool_enabled_turns_stay_on_agent_loop_across_follow_ups(self):
        cases = [
            [
                {"role": "user", "content": "Intent: IMPLEMENT\n\nGoal:\nCreate backend and frontend."},
                {"role": "assistant", "content": "tool call"},
                {"role": "user", "content": "Validate the diff and report status."},
            ],
            [
                {"role": "user", "content": "Crie o projeto MeuFazTudo com backend e frontend Angular."},
                {"role": "user", "content": "[KITT COMPLETION VERIFICATION]\nCheck files now."},
            ],
            [
                {"role": "user", "content": "Intent: REFACTOR\n\nGoal:\nRefactor backend code."},
                {"role": "user", "content": "Inspect git diff only."},
            ],
            [
                {"role": "user", "content": "任意の言語で要求を書けます。"},
                {"role": "user", "content": "Continue from host evidence."},
            ],
        ]
        for messages in cases:
            with self.subTest(messages=messages):
                self.assertEqual(infer_agent_route(SYSTEM_PROMPT, messages), "agent-loop")


if __name__ == "__main__":
    unittest.main()
