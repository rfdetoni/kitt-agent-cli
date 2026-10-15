"""Regression cases for literal task preservation and multilingual negation."""
import unittest

from kitt.context_filter.context_planner import ContextPlanner
from kitt.context_filter.deterministic_extractor import DeterministicExtractor
from kitt.context_filter.fallback import DeterministicFallbackPlanner
from kitt.context_filter.fidelity import validate_semantic_fidelity
from kitt.domain.entities import SemanticTask


class LiteralSemanticsTests(unittest.TestCase):
    def test_structural_check_never_promotes_classification_confidence(self):
        prompt = "Create a configuration file."
        task = SemanticTask(
            original_prompt=prompt, intent="IMPLEMENT", goal="Create config",
            actions=["edit"], confidence=0.8,
        )
        passed, _ = validate_semantic_fidelity(prompt, task)
        self.assertTrue(passed)
        self.assertEqual(task.confidence, 0.8)
        self.assertLessEqual(task.semantic_confidence.overall, task.confidence)
        self.assertTrue(ContextPlanner().build_plan(task).include_original_prompt)

    def test_contradictory_read_only_interpretation_is_rejected(self):
        prompt = "Explica el código sin modificar ningún archivo."
        task = SemanticTask(
            original_prompt=prompt, intent="IMPLEMENT", goal="Modify the code",
            actions=["edit"], confidence=0.8,
        )
        passed, reason = validate_semantic_fidelity(prompt, task)
        self.assertFalse(passed)
        self.assertIn("contradicts", reason)
        self.assertEqual(task.confidence, 0.8)
        self.assertTrue(ContextPlanner().build_plan(task).include_original_prompt)

    def test_equivalent_file_creation_is_a_mutation(self):
        fallback = DeterministicFallbackPlanner()
        for prompt in (
            "Crie um arquivo de configuração",
            "Create a configuration file",
            "Crea un archivo de configuración",
        ):
            with self.subTest(prompt=prompt):
                task = fallback.generate_task(prompt)
                self.assertEqual(task.intent, "IMPLEMENT")
                self.assertEqual(task.original_prompt, prompt)

    def test_three_languages_preserve_negative_constraint_offsets(self):
        extractor = DeterministicExtractor()
        for prompt in (
            "Não altere a API pública.",
            "Do not change the public API.",
            "No cambies la API pública.",
            "Crea una prueba para UserService sin cambiar la API pública.",
        ):
            with self.subTest(prompt=prompt):
                spans = extractor.extract_constraints(prompt)
                self.assertTrue(spans)
                for span in spans:
                    self.assertEqual(prompt[span.source_start:span.source_end], span.text)

    def test_explanations_and_reviews_do_not_become_write_requests(self):
        fallback = DeterministicFallbackPlanner()
        cases = {
            "Explica el código sin modificar ningún archivo.": "ASK",
            "Revisa UserService; no apliques ninguna corrección.": "REVIEW",
            "Crea una prueba para UserService sin cambiar la API pública.": "IMPLEMENT",
            "Não crie o arquivo; apenas explique o formato.": "ASK",
        }
        for prompt, expected in cases.items():
            with self.subTest(prompt=prompt):
                self.assertEqual(fallback.generate_task(prompt).intent, expected)


if __name__ == "__main__":
    unittest.main()
