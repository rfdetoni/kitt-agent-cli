import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kitt.context_filter.fallback import DeterministicFallbackPlanner
from kitt.core.completion_guard import (
    build_completion_contract,
    requires_workspace_mutation,
)


MEUFAZTUDO_PROMPT = (
    'crie um site moderno e limpo para registrar prestadores de serviço, será chamado '
    'meufaztudo e juntará maridos de aluguel a pessoas que precisam contratar o serviço, '
    'crie pasta de backend com o conteudo de backend e pasta de front end com todo o front '
    'em angular. Crie o projeto e a implementação'
)


class MeufaztudoCompletionRegressionTests(unittest.TestCase):
    def test_explicit_creation_request_overrides_read_only_semantic_misclassification(self):
        task = SimpleNamespace(
            intent='ASK',
            actions=[],
            original_prompt=MEUFAZTUDO_PROMPT,
        )
        processor = SimpleNamespace(session_state=SimpleNamespace(last_task=task))
        cmd = SimpleNamespace(mode='auto', prompt=MEUFAZTUDO_PROMPT)

        self.assertTrue(requires_workspace_mutation(processor, cmd))

    def test_deterministic_planner_expands_full_stack_creation_into_execution_steps(self):
        task = DeterministicFallbackPlanner().generate_task(MEUFAZTUDO_PROMPT)
        actions = '\n'.join(task.actions)

        self.assertEqual(task.intent, 'IMPLEMENT')
        self.assertIn('workspace structure', actions)
        self.assertIn('ordered execution checklist', actions)
        self.assertIn('backend scope', actions)
        self.assertIn('frontend scope', actions)
        self.assertIn('every changed project scope', actions)
        self.assertTrue(task.validation_hints)

    def test_contract_uses_original_prompt_even_when_semantic_task_is_misclassified(self):
        task = SimpleNamespace(
            intent='REVIEW',
            actions=[],
            original_prompt=MEUFAZTUDO_PROMPT,
        )
        contract = build_completion_contract('continue', task)

        self.assertTrue(contract.enabled)
        self.assertEqual([scope.root for scope in contract.scopes], ['backend', 'frontend'])
        self.assertEqual(contract.validation_scopes, ('backend', 'frontend'))

    def test_backend_only_is_never_enough_for_full_stack_request(self):
        contract = build_completion_contract(MEUFAZTUDO_PROMPT)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'backend' / 'src').mkdir(parents=True)
            (root / 'backend' / 'package.json').write_text('{}\n', encoding='utf-8')
            (root / 'backend' / 'src' / 'index.ts').write_text('export {};\n', encoding='utf-8')

            issues = contract.evaluate(
                root,
                validation_succeeded=True,
                validated_scopes=frozenset({'backend'}),
            )

        self.assertTrue(any('frontend' in issue for issue in issues))

    def test_each_requested_scope_requires_validation(self):
        contract = build_completion_contract(MEUFAZTUDO_PROMPT)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'backend' / 'src').mkdir(parents=True)
            (root / 'frontend' / 'src').mkdir(parents=True)
            (root / 'backend' / 'package.json').write_text('{}\n', encoding='utf-8')
            (root / 'backend' / 'src' / 'index.ts').write_text('export {};\n', encoding='utf-8')
            (root / 'frontend' / 'package.json').write_text('{}\n', encoding='utf-8')
            (root / 'frontend' / 'angular.json').write_text('{}\n', encoding='utf-8')
            (root / 'frontend' / 'src' / 'main.ts').write_text('export {};\n', encoding='utf-8')

            backend_only = contract.evaluate(
                root,
                validation_succeeded=True,
                validated_scopes=frozenset({'backend'}),
            )
            complete = contract.evaluate(
                root,
                validation_succeeded=True,
                validated_scopes=frozenset({'backend', 'frontend'}),
            )

        self.assertTrue(any('frontend has not been validated' in issue for issue in backend_only))
        self.assertEqual(complete, [])


if __name__ == '__main__':
    unittest.main()
