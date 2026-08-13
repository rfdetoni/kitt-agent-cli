import unittest
from kitt.context_filter.prompt_budget import PromptBudget, PromptSections

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

if __name__ == "__main__":
    unittest.main()
