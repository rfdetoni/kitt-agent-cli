import unittest

from kitt.context_filter.semantic_filter import SemanticFilter
from kitt.core.turn_processor import TurnProcessor
from kitt.domain.entities import ModelProfile
from kitt.tools.surface_selector import ToolSurfaceSelector


PROMPT = "converta o backend deste projeto para maven"


class WorkspaceConversionSemanticsTests(unittest.TestCase):
    def test_reverse_proxy_bypass_keeps_workspace_conversion_execution_capable(self):
        profile = ModelProfile(
            backend="kitt-reverse-proxy",
            protocol="kitt-reverse-proxy",
            model="gemini-web",
        )

        result = SemanticFilter(profile).filter_and_plan(PROMPT)

        self.assertEqual(result.source, "DETERMINISTIC_BYPASS")
        self.assertEqual(result.task.intent, "IMPLEMENT")
        self.assertTrue(result.plan.enabled_tools)
        self.assertIn("read_file", result.plan.enabled_tools)
        self.assertIn("write_file", result.plan.enabled_tools)
        self.assertEqual(
            list(ToolSurfaceSelector().select_tools(result.plan)),
            ["kitt_runtime"],
        )

    def test_workspace_conversion_routes_to_code_edit(self):
        profile = ModelProfile(
            backend="kitt-reverse-proxy",
            protocol="kitt-reverse-proxy",
            model="gemini-web",
        )
        result = SemanticFilter(profile).filter_and_plan(PROMPT)

        self.assertEqual(
            TurnProcessor._agent_route_for_task(result.task, "auto", PROMPT),
            "code-edit",
        )

    def test_how_to_conversion_remains_read_only(self):
        profile = ModelProfile(
            backend="kitt-reverse-proxy",
            protocol="kitt-reverse-proxy",
            model="gemini-web",
        )

        result = SemanticFilter(profile).filter_and_plan(
            "como converter um projeto Gradle para Maven?"
        )

        self.assertEqual(result.task.intent, "ASK")
        self.assertEqual(result.plan.enabled_tools, [])


if __name__ == "__main__":
    unittest.main()
