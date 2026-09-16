import unittest

from kitt.context_filter.semantic_filter import SemanticFilter
from kitt.domain.entities import ModelProfile


class _CapturingClient:
    def __init__(self):
        self.session_keys = []

    def chat(self, messages, system_prompt=None, response_format=None, session_key=None):
        self.session_keys.append(session_key)
        raise AssertionError("reverse-proxy semantic planning must stay off browser sessions")


class TestReverseProxyPhaseSession(unittest.TestCase):
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
