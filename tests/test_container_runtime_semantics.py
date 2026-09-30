import unittest

from kitt.context_filter.semantic_filter import SemanticFilter
from kitt.domain.entities import ModelProfile
from kitt.tools.surface_selector import ToolSurfaceSelector


class ContainerRuntimeSemanticsTests(unittest.TestCase):
    @staticmethod
    def _plan(prompt: str):
        profile = ModelProfile(
            backend="kitt-reverse-proxy",
            protocol="kitt-reverse-proxy",
            model="gemini-web",
        )
        return SemanticFilter(profile).filter_and_plan(prompt)

    def test_container_requests_are_not_semantically_classified_by_kitt(self):
        for prompt in (
            "suba os containers com docker compose",
            "reinicie o serviço com podman compose",
            "verifique os pods com kubectl",
            "como usar docker compose neste projeto?",
            "docker compose でコンテナを起動してください",
        ):
            with self.subTest(prompt=prompt):
                result = self._plan(prompt)
                self.assertEqual(result.source, "LLM_FIRST")
                self.assertEqual(result.task.intent, "UNKNOWN")
                self.assertEqual(result.task.technologies, [])
                self.assertEqual(result.task.original_prompt, prompt)
                self.assertTrue(result.plan.include_original_prompt)
                self.assertTrue(result.plan.enabled_tools)
                self.assertIn("run_command", result.plan.enabled_tools)
                self.assertEqual(
                    list(ToolSurfaceSelector().select_tools(result.plan)),
                    ["kitt_runtime"],
                )


if __name__ == "__main__":
    unittest.main()
