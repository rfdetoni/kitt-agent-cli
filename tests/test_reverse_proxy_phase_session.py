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


    def test_reverse_proxy_execution_bypasses_semantic_compilation_and_model_routing(self):
        profile = ModelProfile(
            backend="kitt-reverse-proxy",
            protocol="kitt-reverse-proxy",
            model="chatgpt-web",
            base_url="http://127.0.0.1:3000",
        )
        processor = TurnProcessor.__new__(TurnProcessor)
        processor.context_client = None
        processor.execution_client = None
        processor.router = SimpleNamespace(
            resolve_profile_for_task=lambda _task: ("execute", profile)
        )
        processor.session_state = SimpleNamespace()
        processor.working_set = SimpleNamespace(touch_paths=lambda *args, **kwargs: None)
        processor.config = SimpleNamespace(privacy_mode="hybrid_redacted")

        prompt = "バックエンドを作成せず、要求された範囲だけ変更してください。"
        from kitt.core.turn_command import TurnCommand
        cmd = TurnCommand(
            conversation_id="conversation-llm-first",
            prompt=prompt,
            turn_id="turn-llm-first",
        )

        task, plan, filter_result, _client, _ctx_profile, _addressed = processor._run_semantic_filter(cmd)
        self.assertEqual(filter_result.source, "LLM_FIRST")
        self.assertEqual(task.original_prompt, prompt)
        self.assertEqual(task.intent, "UNKNOWN")
        self.assertEqual(task.actions, [])
        self.assertTrue(plan.include_original_prompt)
        self.assertIn("kitt_runtime", plan.enabled_tools)

        selected_name, selected_profile, decision, blocked = processor._resolve_execution_profile(cmd, task)
        self.assertIsNone(blocked)
        self.assertEqual(selected_name, "execute")
        self.assertEqual(selected_profile.model, "chatgpt-web")
        self.assertEqual(decision.policy_version, "llm-first-v1")
        self.assertIn("natural-language model routing is bypassed", decision.reasons[0])

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
        self.assertEqual(result.source, "LLM_FIRST")
        self.assertEqual(result.task.intent, "UNKNOWN")
        self.assertEqual(result.task.original_prompt, "Analyze the repository and improve the implementation.")


if __name__ == "__main__":
    unittest.main()
