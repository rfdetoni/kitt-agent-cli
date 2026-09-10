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
