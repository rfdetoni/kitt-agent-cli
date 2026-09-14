import unittest

from kitt.context_filter.fallback import (
    DeterministicFallbackPlanner,
    is_workspace_creation_request,
)


class ProjectCreationIntentTests(unittest.TestCase):
    def test_ponytail_full_project_creation_is_implementation(self):
        prompt = (
            "/ponytail crie um site moderno e limpo para registrar prestadores de serviço, "
            "será chamado meufaztudo e juntará maridos de aluguel a pessoas que precisam "
            "contratar o serviço, crie pasta de backend com o conteudo de backend e pasta "
            "de front end com todo o front em angular. Crie o projeto e a implementação"
        )
        planner = DeterministicFallbackPlanner()

        self.assertTrue(is_workspace_creation_request(prompt))
        task = planner.generate_task(prompt)
        self.assertEqual(task.intent, "IMPLEMENT")
        self.assertIn("edit", task.actions)

        plan = planner.generate_plan(task)
        self.assertIn("write_file", plan.enabled_tools)
        self.assertIn("create_directory", plan.enabled_tools)
        self.assertIn("run_command", plan.enabled_tools)

    def test_how_to_create_a_project_remains_a_question(self):
        planner = DeterministicFallbackPlanner()
        task = planner.generate_task("como criar um projeto Angular?")
        self.assertEqual(task.intent, "ASK")


if __name__ == "__main__":
    unittest.main()
