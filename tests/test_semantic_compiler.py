import unittest
import tempfile
from pathlib import Path
from kitt.domain.entities import SemanticTask, Constraint, ContextPlan
from kitt.context_filter.schema import ContextFilterSchemaValidator
from kitt.context_filter.fidelity import validate_semantic_fidelity, IR_ONLY_CONFIDENCE_THRESHOLD
from kitt.context_filter.deterministic_extractor import DeterministicExtractor
from kitt.context_filter.context_planner import ContextPlanner
from kitt.core.turn_processor import TurnProcessor
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import TurnStarted, TurnCompleted
from tests.test_fake_llm_e2e import FakeLLMClient


class TestSemanticCompiler(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp_dir.name).resolve()
        self.extractor = DeterministicExtractor()
        self.planner = ContextPlanner()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_semantic_task_accepts_goal_and_validation_hints(self):
        task = SemanticTask(
            original_prompt="Fix sqlite error",
            intent="DEBUG",
            goal="Fix SQLite database lock issue",
            actions=["protect in-memory transaction with RLock"],
            symbols=["HistoryDatabase", "RLock"],
            paths=["kitt/history/database.py"],
            validation_hints=["run concurrency tests"]
        )
        self.assertEqual(task.goal, "Fix SQLite database lock issue")
        self.assertIn("run concurrency tests", task.validation_hints)
        
        prompt = task.to_execution_prompt()
        self.assertIn("Intent: DEBUG", prompt)
        self.assertIn("Goal:\nFix SQLite database lock issue", prompt)
        self.assertIn("Actions:\n- protect in-memory transaction with RLock", prompt)
        self.assertIn("Targets:\n- kitt/history/database.py\n- HistoryDatabase", prompt)
        self.assertIn("Validation:\n- run concurrency tests", prompt)

    def test_schema_validator_parses_and_bounds_goal_and_hints(self):
        raw_json = """
        {
            "intent": "IMPLEMENT",
            "goal": "Evolve SemanticTask into canonical Task IR",
            "actions": ["add goal and validation fields", "update prompt serializer"],
            "symbols": ["SemanticTask", "ContextPlan"],
            "paths": ["kitt/domain/entities.py"],
            "technologies": ["python"],
            "constraints": [
                {
                    "text": "sem adicionar dependências",
                    "kind": "NEGATIVE",
                    "mandatory": true
                }
            ],
            "validation_hints": ["run pytest tests/"],
            "risk": "LOW",
            "confidence": 0.95
        }
        """
        orig_prompt = "Evolua SemanticTask sem adicionar dependências."
        valid, task, err = ContextFilterSchemaValidator.validate_and_parse_task(raw_json, orig_prompt)
        self.assertTrue(valid, err)
        self.assertEqual(task.intent, "IMPLEMENT")
        self.assertEqual(task.goal, "Evolve SemanticTask into canonical Task IR")
        self.assertEqual(len(task.actions), 2)
        self.assertEqual(task.constraints[0].text, "sem adicionar dependências")
        self.assertEqual(task.validation_hints, ["run pytest tests/"])

    def test_fidelity_requires_paths_symbols_and_constraints(self):
        prompt = "Corrija o arquivo kitt/foo.py sem alterar FooService."
        
        # 1. Complete fidelity pass
        good_task = SemanticTask(
            original_prompt=prompt,
            intent="DEBUG",
            goal="Fix issue in foo module",
            actions=["edit foo.py"],
            paths=["kitt/foo.py"],
            symbols=["FooService"],
            constraints=[Constraint(text="sem alterar FooService", kind="NEGATIVE", source_start=24, source_end=48)],
            confidence=0.95
        )
        passed, reason = validate_semantic_fidelity(prompt, good_task, self.extractor)
        self.assertTrue(passed, reason)

        plan = self.planner.build_plan(good_task, prompt)
        self.assertFalse(plan.include_original_prompt)  # IR_ONLY

        # 2. Missing path forces original
        bad_task_path = SemanticTask(
            original_prompt=prompt,
            intent="DEBUG",
            goal="Fix issue in foo module",
            paths=[],  # Missing kitt/foo.py
            symbols=["FooService"],
            constraints=[Constraint(text="sem alterar FooService", kind="NEGATIVE", source_start=24, source_end=48)],
            confidence=0.95
        )
        passed, reason = validate_semantic_fidelity(prompt, bad_task_path, self.extractor)
        self.assertFalse(passed)
        self.assertIn("kitt/foo.py", reason)

        # 3. Missing negative constraint forces original
        bad_task_constraint = SemanticTask(
            original_prompt=prompt,
            intent="DEBUG",
            goal="Fix issue in foo module",
            paths=["kitt/foo.py"],
            symbols=["FooService"],
            constraints=[],  # Missing negative constraint
            confidence=0.95
        )
        passed, reason = validate_semantic_fidelity(prompt, bad_task_constraint, self.extractor)
        self.assertFalse(passed)
        self.assertIn("constraint", reason.lower())

    def test_linguistic_tasks_force_original_prompt(self):
        prompts = [
            "Traduza o texto abaixo para o inglês",
            "Reescreva este parágrafo em tom formal",
            "Corrija a gramática deste email"
        ]
        for p in prompts:
            self.assertTrue(self.extractor.is_linguistic_task(p))
            task = SemanticTask(
                original_prompt=p,
                intent="DOCUMENT",
                goal="Translate or rewrite text",
                confidence=0.99
            )
            passed, reason = validate_semantic_fidelity(p, task, self.extractor)
            self.assertFalse(passed)
            self.assertIn("Linguistic task", reason)
            plan = self.planner.build_plan(task, p)
            self.assertTrue(plan.include_original_prompt)

    def test_e2e_ir_only_execution_payload(self):
        captured_requests = []

        class CaptureExecutionClient:
            def chat_stream(self, messages, system_prompt=None):
                captured_requests.append({"messages": messages, "system_prompt": system_prompt})
                yield "Plan executed successfully."

        filter_json = """
        {
            "intent": "IMPLEMENT",
            "goal": "Add RLock to in-memory HistoryDatabase transactions",
            "actions": ["wrap memory connection in RLock", "preserve :memory: mode"],
            "symbols": ["HistoryDatabase", "RLock"],
            "paths": ["kitt/history/database.py"],
            "technologies": ["python", "sqlite"],
            "constraints": [
                {
                    "text": "não adicione dependências",
                    "kind": "NEGATIVE",
                    "mandatory": true
                }
            ],
            "validation_hints": ["run concurrency tests"],
            "risk": "LOW",
            "confidence": 0.96
        }
        """
        orig_prompt = "Corrija o acesso concorrente no HistoryDatabase usando RLock em kitt/history/database.py, não adicione dependências."
        
        from kitt.history.service import HistoryService
        history = HistoryService(root_dir=self.tmp_dir.name)
        
        processor = TurnProcessor(
            root_dir=self.tmp_dir.name,
            context_client=FakeLLMClient([filter_json]),
            execution_client=CaptureExecutionClient(),
            history_service=history
        )

        conv = history.get_or_create_active()
        cmd = TurnCommand(conversation_id=conv["id"], prompt=orig_prompt)
        history.repo.save_message(conv["id"], cmd.turn_id, "user", orig_prompt)
        events = list(processor.run_turn(cmd))
        
        self.assertTrue(any(isinstance(e, TurnCompleted) for e in events))
        self.assertEqual(len(captured_requests), 1)
        req = captured_requests[0]
        
        # User message received by execution LLM is compact Task IR
        user_content = req["messages"][0]["content"]
        self.assertIn("Intent: IMPLEMENT", user_content)
        self.assertIn("Goal:\nAdd RLock to in-memory HistoryDatabase transactions", user_content)
        self.assertIn("Targets:\n- kitt/history/database.py\n- HistoryDatabase", user_content)
        self.assertIn("Validation:\n- run concurrency tests", user_content)
        
        # Ensure verbose original prompt is NOT repeated in the user task message
        self.assertNotIn(orig_prompt, user_content)

        # Ensure history persisted the original user prompt
        history_msgs = processor.history_service.repo.get_messages_for_conversation(conv["id"])
        self.assertEqual(history_msgs[0]["content"], orig_prompt)

    def test_low_confidence_retains_original_prompt(self):
        captured_requests = []

        class CaptureExecutionClient:
            def chat_stream(self, messages, system_prompt=None):
                captured_requests.append({"messages": messages, "system_prompt": system_prompt})
                yield "Plan executed."

        filter_json = """
        {
            "intent": "IMPLEMENT",
            "goal": "Uncertain refactoring",
            "actions": ["refactor"],
            "symbols": [],
            "paths": [],
            "technologies": ["python"],
            "constraints": [],
            "validation_hints": [],
            "risk": "HIGH",
            "confidence": 0.55
        }
        """
        orig_prompt = "Refactor everything in uncertain mode."
        processor = TurnProcessor(
            root_dir=self.tmp_dir.name,
            context_client=FakeLLMClient([filter_json]),
            execution_client=CaptureExecutionClient()
        )

        cmd = TurnCommand(conversation_id="conv-low-conf", prompt=orig_prompt)
        list(processor.run_turn(cmd))
        
        self.assertEqual(len(captured_requests), 1)
        user_content = captured_requests[0]["messages"][0]["content"]
        self.assertEqual(user_content, orig_prompt)

    def test_token_reduction_on_verbose_prompt(self):
        from kitt.context_filter.prompt_budget import TokenCounter
        verbose_prompt = (
            "Olá K.I.T.T., bom dia! Por favor, gostaria de pedir uma ajuda muito importante. "
            "Estou enfrentando um bug sério no módulo kitt/history/database.py com acesso concorrente. "
            "Precisamos proteger o HistoryDatabase utilizando RLock para evitar colisões entre threads. "
            "Por favor, certifique-se de manter compatibilidade com a API pública existente e não adicione novas dependências. "
            "Depois de implementar, rode os testes de concorrência para garantir que tudo está funcionando."
        )
        task = SemanticTask(
            original_prompt=verbose_prompt,
            intent="DEBUG",
            goal="Fix concurrent SQLite access in HistoryDatabase",
            actions=["protect shared in-memory transactions with RLock"],
            symbols=["HistoryDatabase", "RLock"],
            paths=["kitt/history/database.py"],
            technologies=["python", "sqlite"],
            constraints=[Constraint(text="não adicione novas dependências", kind="NEGATIVE", source_start=0, source_end=0)],
            validation_hints=["run concurrency tests"],
            confidence=0.95
        )
        compiled = task.to_execution_prompt()
        orig_tokens = TokenCounter.count_tokens(verbose_prompt)
        compiled_tokens = TokenCounter.count_tokens(compiled)
        reduction_pct = (orig_tokens - compiled_tokens) / orig_tokens * 100.0
        
        self.assertGreater(orig_tokens, compiled_tokens)
        self.assertGreaterEqual(reduction_pct, 15.0)

    def test_semantic_task_confidence_and_fingerprint(self):
        task = SemanticTask(
            original_prompt="Fix HistoryDatabase in kitt/history/database.py",
            intent="IMPLEMENT",
            goal="Fix HistoryDatabase",
            paths=["kitt/history/database.py"],
            symbols=["HistoryDatabase"]
        )
        fp1 = task.fingerprint()
        self.assertTrue(bool(fp1))

        # Same semantic content -> same fingerprint
        task2 = SemanticTask(
            original_prompt="Another prompt",
            intent="IMPLEMENT",
            goal="Fix HistoryDatabase",
            paths=["kitt/history/database.py"],
            symbols=["HistoryDatabase"]
        )
        self.assertEqual(fp1, task2.fingerprint())

    def test_retrieval_fallback_includes_original_prompt_when_fidelity_fails(self):
        prompt = "Explain in detail and fix HistoryDatabase in kitt/history/database.py"
        # Incomplete task (missing symbols / linguistic task)
        task = SemanticTask(
            original_prompt=prompt,
            intent="ASK",
            goal="Explain code",
            paths=["kitt/history/database.py"],
            confidence=0.60
        )
        plan = self.planner.build_plan(task, prompt)
        self.assertTrue(plan.include_original_prompt)

        processor = TurnProcessor(root_dir=self.tmp_dir.name)
        # Mocking context build check
        profile = processor.router.resolve_profile_for_task("context-gather")[1]
        _, _, _, _, _, _, _, _ = processor._build_context(
            TurnCommand(conversation_id="c1", prompt=prompt),
            task,
            plan,
            profile,
            None
        )
        # Context engine last query was constructed with original prompt included
        self.assertIn("kitt/history/database.py", task.paths)


if __name__ == "__main__":
    unittest.main()
