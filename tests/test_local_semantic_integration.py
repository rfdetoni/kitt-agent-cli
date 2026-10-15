"""Behavioral guard tests for the optional local semantic route."""
import unittest
from unittest.mock import patch

from kitt.context.rag.embeddings import OllamaEmbeddingProvider
from kitt.context_filter.semantic_filter import SemanticFilter
from kitt.context_filter.local_intent import deterministic_intent
from kitt.domain.entities import ModelProfile, SemanticTask


class FakeContextLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, *args, **kwargs):
        self.calls += 1
        return (
            '{"intent":"ASK","goal":"Explain without edits","actions":["answer"],'
            '"confidence":0.8,"constraints":[]}'
        )


class FakeLocal:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def classify(self, prompt):
        self.calls += 1
        return self.result


class LocalSemanticIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.profile = ModelProfile(backend="ollama", model="context-dummy")
        self.env = patch.dict("os.environ", {"KITT_SEMANTIC_LOCAL_MODEL": "test-local-model"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_rule_fast_path_needs_no_ollama_model_or_llm(self):
        with patch.dict("os.environ", {"KITT_SEMANTIC_LOCAL_MODEL": ""}):
            llm = FakeContextLLM()
            with patch("kitt.context_filter.semantic_filter.get_local_classifier") as provider:
                result = SemanticFilter(self.profile, llm, local_mode="auto").filter_and_plan(
                    "Crea un archivo de configuración."
                )
            self.assertEqual(result.source, "DETERMINISTIC_BYPASS")
            self.assertEqual(result.task.intent, "IMPLEMENT")
            self.assertEqual(result.task.original_prompt, "Crea un archivo de configuración.")
            self.assertEqual(llm.calls, 0)
            provider.assert_not_called()

    def test_local_classification_skips_generating_context_model_call(self):
        prompt = "I want an explanation of UserService without editing files."
        task = SemanticTask(
            original_prompt=prompt, intent="ASK", goal=prompt,
            actions=["analyze"], confidence=0.65,
        )
        local = FakeLocal((task, "accepted"))
        llm = FakeContextLLM()
        with patch("kitt.context_filter.semantic_filter.get_local_classifier", return_value=local):
            result = SemanticFilter(self.profile, llm, local_mode="auto").filter_and_plan(prompt)
        self.assertEqual(result.source, "LOCAL")
        self.assertEqual(local.calls, 1)
        self.assertEqual(llm.calls, 0)
        self.assertTrue(result.plan.include_original_prompt)

    def test_ambiguous_uses_existing_llm_once(self):
        llm = FakeContextLLM()
        local = FakeLocal((None, "score_or_margin"))
        with patch("kitt.context_filter.semantic_filter.get_local_classifier", return_value=local):
            result = SemanticFilter(self.profile, llm, local_mode="auto").filter_and_plan(
                "Could you explain why UserService returns HTTP 403?"
            )
        self.assertEqual(result.source, "FALLBACK")
        self.assertIn("fidelity_rejected", result.fallback_reason or "")
        self.assertEqual(llm.calls, 1)
        self.assertTrue(result.plan.include_original_prompt)

    def test_llm_read_only_mutation_conflict_falls_back_without_write_tools(self):
        class ContradictoryLLM:
            def chat(self, *args, **kwargs):
                return ('{"intent":"IMPLEMENT","goal":"Modify the code",'
                        '"actions":["edit"],"confidence":0.8,"constraints":[]}')
        prompt = "Explica el código sin modificar ningún archivo."
        result = SemanticFilter(self.profile, ContradictoryLLM(), local_mode="off").filter_and_plan(prompt)
        self.assertEqual(result.source, "FALLBACK")
        self.assertEqual(result.task.original_prompt, prompt)
        self.assertEqual(result.task.intent, "ASK")
        self.assertTrue(result.plan.include_original_prompt)
        self.assertTrue(set(result.plan.enabled_tools).isdisjoint({
            "run_command", "write_file", "apply_patch", "create_directory",
        }))

    def test_required_local_abstains_without_generating_request(self):
        llm = FakeContextLLM()
        local = FakeLocal((None, "conversation_dependency"))
        with patch("kitt.context_filter.semantic_filter.get_local_classifier", return_value=local):
            result = SemanticFilter(self.profile, llm, local_mode="local").filter_and_plan(
                "Continue a correção anterior do UserService"
            )
        self.assertEqual(result.source, "ABSTAIN")
        self.assertEqual(llm.calls, 0)
        self.assertEqual(result.fallback_reason, "conversation_dependency")

    def test_conservative_direct_intents_in_three_languages(self):
        cases = {
            "Crie o arquivo de configuração.": "IMPLEMENT",
            "Create a configuration file.": "IMPLEMENT",
            "Crea un archivo de configuración.": "IMPLEMENT",
            "Explain how to fix the bug without editing files.": "ASK",
            "Explica el código sin modificar ningún archivo.": "ASK",
            "Fix the authentication bug and run the relevant tests.": "DEBUG",
            "Revisa UserService; no apliques ninguna corrección.": "REVIEW",
            "How to create a configuration file?": "ASK",
            "Refatore BillingService para simplificar.": "REFACTOR",
        }
        for prompt, intent in cases.items():
            with self.subTest(prompt=prompt):
                self.assertEqual(deterministic_intent(prompt), intent)
        for prompt in ("continue", "do the same", "faça isso",
                       "Create a test and then refactor the module."):
            self.assertIsNone(deterministic_intent(prompt))

    def test_reverse_proxy_never_uses_local_classifier(self):
        prompt = "Crea el archivo sin cambiar la API."
        proxy = ModelProfile(backend="kitt-reverse-proxy", model="webchat")
        with patch("kitt.context_filter.semantic_filter.get_local_classifier") as local:
            result = SemanticFilter(proxy, local_mode="auto").filter_and_plan(prompt)
        self.assertEqual(result.source, "LLM_FIRST")
        self.assertEqual(result.task.original_prompt, prompt)
        self.assertTrue(result.plan.include_original_prompt)
        local.assert_not_called()

    def test_embedders_reject_remote_egress(self):
        for url in ("https://api.example.org:11434", "http://127.0.0.1.evil.test:11434",
                    "http://user:pass@127.0.0.1:11434", "http://localhost:11434/other"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                OllamaEmbeddingProvider("qllama/multilingual-e5-small:q8_0", base_url=url)

    def test_e5_role_prefixes_are_distinct_and_other_models_unmodified(self):
        for model, expected_query, expected_doc in (
            ("qllama/multilingual-e5-small:q8_0", "query: foo", "passage: foo"),
            ("bge-m3", "foo", "foo"),
        ):
            with self.subTest(model=model):
                provider = OllamaEmbeddingProvider(model, base_url="http://127.0.0.1:11434")
                captured = []
                def fake_batch(texts, timeout):
                    captured.extend(texts)
                    return [[1.0, 0.0] for _ in texts]
                provider._embed_batch = fake_batch
                provider.embed_queries(["foo"])
                provider.embed_documents(["foo"])
                self.assertEqual(captured, [expected_query, expected_doc])


if __name__ == "__main__":
    unittest.main()
