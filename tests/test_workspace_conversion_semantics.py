import unittest

from kitt.context_filter.semantic_filter import SemanticFilter
from kitt.domain.entities import ModelProfile
from kitt.tools.surface_selector import ToolSurfaceSelector


PROMPT = "converta o backend deste projeto para maven"


class WorkspaceConversionSemanticsTests(unittest.TestCase):
    @staticmethod
    def _profile() -> ModelProfile:
        return ModelProfile(
            backend="kitt-reverse-proxy",
            protocol="kitt-reverse-proxy",
            model="gemini-web",
        )

    def test_reverse_proxy_bypass_is_llm_first_and_execution_capable(self):
        result = SemanticFilter(self._profile()).filter_and_plan(PROMPT)

        self.assertEqual(result.source, "LLM_FIRST")
        self.assertEqual(result.task.intent, "UNKNOWN")
        self.assertEqual(result.task.goal, "")
        self.assertEqual(result.task.actions, [])
        self.assertEqual(result.task.original_prompt, PROMPT)
        self.assertTrue(result.plan.include_original_prompt)
        self.assertTrue(result.plan.enabled_tools)
        self.assertEqual(
            list(ToolSurfaceSelector().select_tools(result.plan)),
            ["kitt_runtime"],
        )

    def test_reverse_proxy_does_not_classify_conversion_language(self):
        for prompt in (
            "converta o backend deste projeto para maven",
            "convert this project from Gradle to Maven",
            "このプロジェクトを Maven に変換してください",
            "Wie konvertiere ich dieses Projekt nach Maven?",
        ):
            with self.subTest(prompt=prompt):
                result = SemanticFilter(self._profile()).filter_and_plan(prompt)
                self.assertEqual(result.source, "LLM_FIRST")
                self.assertEqual(result.task.intent, "UNKNOWN")
                self.assertEqual(result.task.original_prompt, prompt)

    def test_informational_reverse_proxy_request_is_left_for_webchat_to_decide(self):
        prompt = "como converter um projeto Gradle para Maven?"
        result = SemanticFilter(self._profile()).filter_and_plan(prompt)

        self.assertEqual(result.source, "LLM_FIRST")
        self.assertEqual(result.task.intent, "UNKNOWN")
        self.assertTrue(result.plan.enabled_tools)
        self.assertEqual(result.task.original_prompt, prompt)


if __name__ == "__main__":
    unittest.main()
