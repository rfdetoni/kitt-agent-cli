import unittest
from kitt.domain.entities import ModelProfile, SemanticTask, ContextPlan, Constraint
from kitt.context_filter.deterministic_extractor import DeterministicExtractor
from kitt.context_filter.schema import ContextFilterSchemaValidator
from kitt.context_filter.fallback import DeterministicFallbackPlanner
from kitt.context_filter.context_planner import ContextPlanner
from kitt.context_filter.semantic_filter import SemanticFilter
from kitt.context_filter.prompt_budget import PromptBudget, TokenCounter

class TestPhase1ContextFilter(unittest.TestCase):
    def setUp(self):
        self.extractor = DeterministicExtractor()
        self.planner = ContextPlanner()
        self.fallback = DeterministicFallbackPlanner()

    def test_deterministic_extractor(self):
        text = "Refactor kitt/cli/repl.py and ProcessTurn without breaking tests."
        paths = self.extractor.extract_paths(text)
        symbols = self.extractor.extract_symbols(text)
        constraints = self.extractor.extract_constraints(text)

        self.assertIn("kitt/cli/repl.py", paths)
        self.assertIn("ProcessTurn", symbols)
        self.assertTrue(any(c.kind == 'NEGATIVE' for c in constraints))

    def test_schema_validation_and_constraint_spans(self):
        prompt = "Fix bug in kitt/edit_format/applier.py without changing DiffApplier signature."
        raw_json = """{
            "intent": "DEBUG",
            "symbols": ["DiffApplier"],
            "paths": ["kitt/edit_format/applier.py"],
            "constraints": [
                {
                    "text": "without changing DiffApplier signature",
                    "kind": "NEGATIVE",
                    "source_start": 41,
                    "source_end": 79,
                    "mandatory": true
                }
            ],
            "confidence": 0.95
        }"""
        valid, task, err = ContextFilterSchemaValidator.validate_and_parse_task(raw_json, prompt)
        self.assertTrue(valid)
        self.assertEqual(task.intent, "DEBUG")
        self.assertEqual(len(task.constraints), 1)
        self.assertEqual(task.constraints[0].text, "without changing DiffApplier signature")

    def test_trivial_prompt_bypass(self):
        text = "/add kitt/cli/repl.py"
        self.assertTrue(self.extractor.is_trivial_prompt(text))

        task = self.fallback.generate_task(text)
        plan = self.fallback.generate_plan(task)
        self.assertEqual(task.confidence, 1.0)
        self.assertIn("kitt/cli/repl.py", task.paths)

    def test_prompt_budget_output_reservation(self):
        budget = PromptBudget(window_size=8192, reserved_output=1200)
        self.assertEqual(budget.reserved_output, 1200)

        sys_p = "You are K.I.T.T."
        task_p = "Refactor code"
        repo_map = "class DiffApplier:\n  def apply(): pass\n" * 100
        files_ctx = "--- file.py ---\n" + ("x = 1\n" * 500)

        alloc = budget.allocate_context(
            system_prompt=sys_p,
            task_prompt=task_p,
            mandatory_constraints=["without breaking API"],
            repo_map=repo_map,
            files_context=files_ctx,
            history_context="",
            recent_results=""
        )

        self.assertIn("system_prompt", alloc)
        self.assertEqual(alloc["reserved_output_tokens"], 1200)
        self.assertLessEqual(alloc["telemetry"].section_tokens["files"], budget.max_files)

    def test_context_planner_tool_selection(self):
        task = SemanticTask(
            original_prompt="Implement new feature in app.py",
            intent="IMPLEMENT",
            paths=["app.py"]
        )
        plan = self.planner.build_plan(task)
        self.assertIn("apply_patch", plan.enabled_tools)
        self.assertIn("run_command", plan.enabled_tools)

if __name__ == '__main__':
    unittest.main()
