import unittest
from kitt.context_filter.prompt_budget import PromptBudget, PromptSections
from kitt.core.turn_context import TurnContextMixin
from kitt.domain.entities import SemanticTask

class TestPromptNoDuplication(unittest.TestCase):
    def test_sentinel_prompt_occurs_once_in_payload(self):
        budget = PromptBudget(window_size=8192)
        sentinel = "SENTINEL_UNIQUE_PROMPT_12345_KITT"
        res = budget.allocate_context(
            system_prompt="System instructions",
            task_prompt=sentinel,
            mandatory_constraints=["Do not break build", "Must pass tests"],
            repo_map="",
            files_context="",
            history_context="",
            recent_results=""
        )

        self.assertIn("sections", res)
        sections: PromptSections = res["sections"]
        self.assertEqual(sections.user_prompt, sentinel)
        self.assertNotIn(sentinel, sections.constraints_text)
        self.assertNotIn(sentinel, res["constraints_text"])
        self.assertIn("Do not break build", res["constraints_text"])

    def test_empty_and_multilingual_prompts(self):
        budget = PromptBudget(window_size=8192)
        for p in ["", "Instrução em Português com acentuação e KITT", "Multi\nline\nmarkdown\n*bold*"]:
            res = budget.allocate_context(
                system_prompt="Sys",
                task_prompt=p,
                mandatory_constraints=[],
                repo_map="",
                files_context="",
                history_context="",
                recent_results=""
            )
            self.assertEqual(res["user_prompt"], p)
            self.assertEqual(res["constraints_text"], "")

    def test_semantic_goal_deduplicates_nearly_identical_original_request(self):
        prompt = (
            "crie um site usando angular 21 ou 22 se possível, usando DDD com mocks para uma "
            "aplicação web de maridos de aluguel chamada faztudo, com login, cadastro de serviços, "
            "compradores, vendedores e avaliações dos prestadores"
        )
        task = SemanticTask(
            original_prompt=prompt,
            intent="IMPLEMENT",
            goal=prompt.replace("chamada faztudo", "chamada faztudo"),
            confidence=0.98,
        )
        self.assertTrue(TurnContextMixin._goal_preserves_original_request(task, prompt))

    def test_semantic_goal_keeps_original_when_requirements_are_not_equivalent(self):
        prompt = (
            "crie um site angular com login, cadastro de compradores, vendedores, serviços "
            "e avaliação do prestador"
        )
        task = SemanticTask(
            original_prompt=prompt,
            intent="IMPLEMENT",
            goal="crie um site angular com login",
            confidence=0.98,
        )
        self.assertFalse(TurnContextMixin._goal_preserves_original_request(task, prompt))


if __name__ == "__main__":
    unittest.main()
