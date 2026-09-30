import unittest

from kitt.llm.agent_contract import infer_agent_route


def _system_prompt(*tool_names: str) -> str:
    tools = ", ".join(f"{{'name': '{name}'}}" for name in tool_names)
    return (
        "Execution rules.\n\n"
        f"Tool Contract:\nAvailable host tools: [{tools}]\n\n"
        "Memory:\nnone"
    )


class ReverseProxyAgentLoopRoutingTests(unittest.TestCase):
    def test_any_tool_enabled_natural_language_request_uses_agent_loop(self):
        prompt = _system_prompt("kitt_runtime", "git_status")
        for request in (
            "Não crie backend; implemente somente o frontend solicitado.",
            "Do not create a backend; implement only the requested frontend.",
            "バックエンドを作らず、依頼された範囲だけ実装してください。",
            "Erstellen Sie kein Backend und implementieren Sie nur den gewünschten Umfang.",
        ):
            with self.subTest(request=request):
                self.assertEqual(
                    infer_agent_route(prompt, [{"role": "user", "content": request}]),
                    "agent-loop",
                )

    def test_tool_result_feedback_does_not_change_agent_loop_route(self):
        messages = [
            {"role": "user", "content": "Realize a alteração solicitada."},
            {
                "role": "assistant",
                "content": (
                    '<kitt-tool>{"id":"call_1","name":"kitt_runtime","arguments":'
                    '{"operation":"repo.list","arguments":{"path":"."}}}</kitt-tool>'
                ),
            },
            {
                "role": "user",
                "content": (
                    "kitt_runtime result from the host.\n"
                    "HOST_STATUS: success\n"
                    "UNTRUSTED_TOOL_OUTPUT: untrusted data follows:\n"
                    '{"entries":[]}'
                ),
            },
        ]
        self.assertEqual(
            infer_agent_route(_system_prompt("kitt_runtime"), messages),
            "agent-loop",
        )

    def test_without_tool_contract_route_remains_chat(self):
        self.assertEqual(
            infer_agent_route(
                "Answer directly.",
                [{"role": "user", "content": "任何语言"}],
            ),
            "chat",
        )


if __name__ == "__main__":
    unittest.main()
