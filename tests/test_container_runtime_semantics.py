import unittest

from kitt.context_filter.semantic_filter import SemanticFilter
from kitt.core.turn_processor import TurnProcessor
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

    def test_docker_compose_lifecycle_is_execution_capable(self):
        prompt = "suba os containers com docker compose"
        result = self._plan(prompt)

        self.assertEqual(result.source, "DETERMINISTIC_BYPASS")
        self.assertEqual(result.task.intent, "TEST")
        self.assertIn("docker", result.task.technologies)
        self.assertIn("run_command", result.plan.enabled_tools)
        self.assertNotIn("write_file", result.plan.enabled_tools)
        self.assertEqual(
            list(ToolSurfaceSelector().select_tools(result.plan)),
            ["kitt_runtime"],
        )
        self.assertEqual(
            TurnProcessor._agent_route_for_task(result.task, "auto", prompt),
            "validate-diff",
        )

    def test_podman_and_kubernetes_operations_are_execution_capable(self):
        for prompt, technology in (
            ("reinicie o serviço com podman compose", "podman"),
            ("verifique os pods com kubectl", "kubernetes"),
        ):
            with self.subTest(prompt=prompt):
                result = self._plan(prompt)
                self.assertEqual(result.task.intent, "TEST")
                self.assertIn(technology, result.task.technologies)
                self.assertIn("run_command", result.plan.enabled_tools)
                self.assertEqual(
                    TurnProcessor._agent_route_for_task(result.task, "auto", prompt),
                    "validate-diff",
                )

    def test_container_how_to_remains_direct_chat(self):
        result = self._plan("como usar docker compose neste projeto?")
        self.assertEqual(result.task.intent, "ASK")
        self.assertEqual(result.plan.enabled_tools, [])


if __name__ == "__main__":
    unittest.main()
