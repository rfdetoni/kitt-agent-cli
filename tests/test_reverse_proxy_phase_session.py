import unittest
from types import SimpleNamespace

from kitt.context_filter.semantic_filter import SemanticFilter
from kitt.core.turn_processor import TurnProcessor
from kitt.domain.entities import ModelProfile


class _CapturingClient:
    def __init__(self):
        self.session_keys = []

    def chat(self, messages, system_prompt=None, response_format=None, session_key=None):
        self.session_keys.append(session_key)
        raise AssertionError("reverse-proxy semantic planning must stay off browser sessions")


class _CapturingStreamClient:
    def __init__(self):
        self.profile = SimpleNamespace(model="chatgpt-web")
        self.routes = []

    def chat_stream(
        self,
        messages,
        system_prompt=None,
        response_format=None,
        session_key=None,
        reasoning_effort=None,
        route=None,
        loop_action_budget=4,
    ):
        self.routes.append((route, loop_action_budget))
        yield '{"action":"final_response","tool":null,"tool_input":null,"content":"ok","reasoning_summary":""}'


class TestReverseProxyPhaseSession(unittest.TestCase):
    def test_execution_route_uses_explicit_task_state_not_prompt_words(self):
        processor = TurnProcessor.__new__(TurnProcessor)
        processor.reasoning_effort = 50
        processor._record_latency = lambda *args, **kwargs: None

        self.assertEqual(
            processor._agent_route_for_task(SimpleNamespace(intent="IMPLEMENT")),
            "code-generation",
        )
        self.assertEqual(
            processor._agent_route_for_task(SimpleNamespace(intent="DEBUG")),
            "code-edit",
        )
        self.assertEqual(
            processor._agent_route_for_task(SimpleNamespace(intent="TEST")),
            "validate-diff",
        )
        self.assertEqual(
            processor._agent_route_for_task(
                SimpleNamespace(intent="TEST"),
                prompt="crie um projeto completo",
            ),
            "validate-diff",
        )

        client = _CapturingStreamClient()
        list(
            processor._stream_execution_response(
                client,
                [{"role": "user", "content": "任意の言語の要求"}],
                "system",
                route="agent-loop",
                loop_action_budget=5,
            )
        )
        self.assertEqual(client.routes, [("agent-loop", 5)])


    def test_reverse_proxy_session_is_scoped_by_logical_conversation(self):
        processor = TurnProcessor.__new__(TurnProcessor)
        processor._proxy_session_key = "agent-window:test"
        profile = SimpleNamespace(
            backend="kitt-reverse-proxy",
            protocol="kitt-reverse-proxy",
        )

        first = processor._provider_session_key(profile, "conversation-a")
        same = processor._provider_session_key(profile, "conversation-a")
        other = processor._provider_session_key(profile, "conversation-b")

        self.assertEqual(first, same)
        self.assertNotEqual(first, other)
        self.assertIn("conversation-a", first)

    def test_semantic_filter_keeps_browser_session_out_of_hidden_planning(self):
        client = _CapturingClient()
        profile = ModelProfile(backend="kitt-reverse-proxy", model="chatgpt-web")
        semantic = SemanticFilter(context_profile=profile, llm_client=client)

        result = semantic.filter_and_plan(
            "Analyze the repository and improve the implementation.",
            session_key="conversation-123",
        )

        self.assertEqual(client.session_keys, [])
        self.assertIsNone(semantic.llm_client)
        self.assertEqual(result.source, "DETERMINISTIC_BYPASS")


if __name__ == "__main__":
    unittest.main()
