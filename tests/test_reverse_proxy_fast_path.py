import unittest

from kitt.context_filter.semantic_filter import SemanticFilter
from kitt.core.turn_processor import _same_reverse_proxy_endpoint
from kitt.domain.entities import ModelProfile
from kitt.llm.client import LLMClient


class _FailIfCalled:
    def chat(self, *args, **kwargs):
        raise AssertionError("context LLM must not be called on deterministic fast path")


class TestReverseProxyFastPath(unittest.TestCase):
    def test_deterministic_semantic_filter_does_not_call_llm(self):
        profile = ModelProfile(backend="kitt-reverse-proxy", model="chatgpt-web", base_url="http://127.0.0.1:3000")
        result = SemanticFilter(profile, _FailIfCalled()).filter_and_plan(
            "crie uma pasta teste", session_key="conv", deterministic_only=True
        )
        self.assertEqual(result.source, "DETERMINISTIC_BYPASS")

    def test_reverse_proxy_profile_never_consumes_browser_turn_for_semantic_planning(self):
        profile = ModelProfile(
            backend="kitt-reverse-proxy",
            model="gemini-web",
            base_url="http://127.0.0.1:3000",
            supports_tools=False,
        )
        prompt = (
            'crie um site moderno e limpo chamado meufaztudo, crie pasta de backend '
            'com o conteudo de backend e pasta de front end com todo o front em angular. '
            'Crie o projeto e a implementação'
        )

        result = SemanticFilter(profile, _FailIfCalled()).filter_and_plan(
            prompt, session_key="conv"
        )

        self.assertEqual(result.source, "DETERMINISTIC_BYPASS")
        self.assertEqual(result.task.intent, "IMPLEMENT")
        self.assertIn("repository_map", result.plan.enabled_tools)
        self.assertIn("write_file", result.plan.enabled_tools)
        self.assertIn("run_command", result.plan.enabled_tools)

    def test_reverse_proxy_protocol_alias_also_bypasses_semantic_llm(self):
        profile = ModelProfile(
            backend="custom",
            protocol="kitt-reverse-proxy",
            model="gemini-web",
            base_url="http://127.0.0.1:3000",
            supports_tools=False,
        )
        result = SemanticFilter(profile, _FailIfCalled()).filter_and_plan(
            "crie uma pasta teste", session_key="conv"
        )
        self.assertEqual(result.source, "DETERMINISTIC_BYPASS")
        self.assertTrue(result.plan.enabled_tools)

    def test_same_reverse_proxy_endpoint_ignores_profile_role(self):
        context = ModelProfile(backend="kitt-reverse-proxy", model="chatgpt-web", base_url="http://127.0.0.1:3000/")
        execute = ModelProfile(backend="kitt-reverse-proxy", model="chatgpt-web", base_url="http://127.0.0.1:3000")
        self.assertTrue(_same_reverse_proxy_endpoint(context, execute))

    def test_reverse_proxy_retry_budget_is_bounded(self):
        profile = ModelProfile(backend="kitt-reverse-proxy", model="chatgpt-web", base_url="http://127.0.0.1:3000")
        client = LLMClient(profile)
        try:
            self.assertEqual(client.retry_policy.config.max_retries, 2)
            self.assertEqual(client.retry_policy.config.max_delay_ms, 1500)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
