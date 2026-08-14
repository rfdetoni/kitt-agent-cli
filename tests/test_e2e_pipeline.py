import unittest
import tempfile
from pathlib import Path
from kitt.core.turn_processor import TurnProcessor
from kitt.edit_format.applier import DiffApplier
from kitt.domain.entities import EditBlock

class TestE2EPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp_dir.name).resolve()
        self.processor = TurnProcessor(root_dir=self.tmp_dir.name)
        self.applier = DiffApplier()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_turn_processor_e2e(self):
        # Create a sample python file
        app_file = self.root_path / "app.py"
        app_file.write_text("def hello(): return 'world'\n", encoding='utf-8')

        from kitt.core.turn_command import TurnCommand
        from kitt.core.turn_events import FilterCompleted, BudgetApplied
        
        cmd = TurnCommand(conversation_id="conv-1", prompt="Refactor app.py to return hello K.I.T.T.", explicit_files={"app.py"})
        
        filter_res = None
        allocated = None
        
        for event in self.processor.run_turn(cmd):
            if isinstance(event, FilterCompleted):
                filter_res = event.filter_res
            elif isinstance(event, BudgetApplied):
                allocated = event.total_input_tokens
                break

        self.assertIsNotNone(filter_res)
        self.assertIsNotNone(allocated)

        # Apply edit block
        block = EditBlock(
            file_path="app.py",
            search_content="def hello(): return 'world'",
            replace_content="def hello(): return 'hello K.I.T.T.'"
        )
        res = self.applier.apply([block], root_dir=self.tmp_dir.name)
        self.assertTrue(res.success)
        self.assertEqual(app_file.read_text(), "def hello(): return 'hello K.I.T.T.'\n")

    def test_context_build_completed_event_is_emitted_for_project_context(self):
        from kitt.core.turn_command import TurnCommand
        from kitt.core.turn_events import ContextBuildCompleted
        from kitt.context_engine.engine import ContextEngine
        from kitt.index.repository import RepositoryIndex

        (self.root_path / "app.py").write_text("def useful_symbol():\n    return 42\n", encoding="utf-8")
        index = RepositoryIndex(self.tmp_dir.name, in_memory=True)
        self.processor.context_engine = ContextEngine(repository_index=index)
        self.processor.enable_context_summary = True

        events = list(self.processor.run_turn(TurnCommand("conv-context-event", "analise useful_symbol neste projeto")))
        event = next(item for item in events if isinstance(item, ContextBuildCompleted))

        self.assertGreaterEqual(event.selected_count, 1)
        self.assertGreater(event.total_tokens, 0)
        self.assertGreaterEqual(event.index_generation, 1)
        self.assertEqual(event.index_state, "READY")
        self.assertEqual(event.schema_version, "2")
        self.assertTrue(event.freshness)
        index.close()

    def test_working_set_is_added_to_later_project_context(self):
        from kitt.core.turn_command import TurnCommand

        captured = {}
        self.processor.enable_context_summary = True
        (self.root_path / "pkg").mkdir()
        (self.root_path / "pkg" / "note.txt").write_text("working note\n", encoding="utf-8")
        (self.root_path / "pkg" / "AGENTS.md").write_text("Use local package guideline.\n", encoding="utf-8")
        self.processor.working_set.touch_paths("conv-working", ["pkg/note.txt"], "turn-prev", weight=2.0, kind="read_file")

        class ExecutionClient:
            def chat_stream(self, messages, system_prompt=None):
                captured["system_prompt"] = system_prompt
                yield "done"

        self.processor.execution_client = ExecutionClient()
        list(self.processor.run_turn(TurnCommand("conv-working", "analise este projeto")))

        self.assertIn("Working Set:", captured["system_prompt"])
        self.assertIn("pkg/note.txt", captured["system_prompt"])
        self.assertIn("Use local package guideline.", captured["system_prompt"])

    def test_tool_output_is_trimmed_to_execution_budget(self):
        profile = type("Profile", (), {"context_window": 900, "max_output_tokens": 300})()
        output = self.processor._fit_tool_output(
            "system",
            [{"role": "user", "content": "x" * 1200}],
            "y" * 4000,
            profile,
        )

        self.assertLess(len(output), 4000)
        self.assertIn("[truncated]", output)

    def test_simple_ask_skips_repository_context(self):
        class FakeExecutionClient:
            def chat_stream(self, *args, **kwargs):
                yield "Hello from K.I.T.T."

        self.processor.execution_client = FakeExecutionClient()
        self.processor.context_engine.get_relevant_context = lambda *args, **kwargs: self.fail("repository context loaded")
        self.processor.context_resolver.resolve_agents_instructions = lambda: self.fail("AGENTS.md loaded")

        from kitt.core.turn_command import TurnCommand
        from kitt.core.turn_events import TextDelta, TurnCompleted
        events = list(self.processor.run_turn(TurnCommand(conversation_id="conv-ask", prompt="hello")))
        self.assertTrue(any(isinstance(event, TurnCompleted) for event in events))

    def test_model_question_reaches_llm_without_agent_prompt(self):
        captured = {}
        class DirectClient:
            def chat_stream(self, messages, system_prompt=None):
                captured["messages"] = messages
                captured["system_prompt"] = system_prompt
                yield "direct model response"

        self.processor.execution_client = DirectClient()
        from kitt.core.turn_events import TurnCompleted
        from kitt.core.turn_command import TurnCommand
        events = list(self.processor.run_turn(TurnCommand("conv-model", "what model is running?")))
        response = next(event.response for event in events if isinstance(event, TurnCompleted))
        self.assertEqual(response, "direct model response")
        self.assertEqual(captured["system_prompt"], "Answer in one direct, concise sentence. Do not expose reasoning.")
        self.assertEqual(captured["messages"], [{"role": "user", "content": "what model is running?"}])

    def test_final_answer_uses_configured_principal_model(self):
        from dataclasses import replace
        from kitt.core.turn_command import TurnCommand
        from kitt.core.turn_events import ModelSelected

        self.processor.router.config.profiles["context"] = replace(
            self.processor.router.config.profiles["context"], model="context-model"
        )
        self.processor.router.config.profiles["execute"] = replace(
            self.processor.router.config.profiles["execute"], model="principal-model"
        )

        class ExecutionClient:
            def chat_stream(self, *args, **kwargs):
                yield "final answer"

        self.processor.execution_client = ExecutionClient()
        events = list(self.processor.run_turn(TurnCommand("conv-models", "analise este projeto")))
        selected = next(event for event in events if isinstance(event, ModelSelected))
        self.assertEqual((selected.profile_name, selected.model), ("execute", "principal-model"))

    def test_policy_blocks_remote_principal_when_offline(self):
        from dataclasses import replace
        from kitt.core.turn_command import TurnCommand
        from kitt.core.turn_events import ModelSelected, TurnBlocked

        self.processor.config = replace(self.processor.config, privacy_mode="offline")
        self.processor.router.config.profiles["execute"] = replace(
            self.processor.router.config.profiles["execute"], backend="openai", model="cloud-model"
        )

        events = list(self.processor.run_turn(TurnCommand("conv-offline", "hello")))

        self.assertTrue(any(isinstance(event, TurnBlocked) for event in events))
        self.assertFalse(any(isinstance(event, ModelSelected) for event in events))

    def test_uncited_code_request_reaches_llm_without_agent_tools(self):
        captured = {}
        class DirectClient:
            def chat_stream(self, messages, system_prompt=None):
                captured["system_prompt"] = system_prompt
                yield "<html></html>"

        self.processor.execution_client = DirectClient()
        from kitt.core.turn_command import TurnCommand
        list(self.processor.run_turn(TurnCommand("conv-html", "Crie uma pagina html.")))
        self.assertEqual(captured["system_prompt"], "Answer in one direct, concise sentence. Do not expose reasoning.")

    def test_context_model_condenses_project_before_principal_model(self):
        (self.root_path / "app.py").write_text("def main():\n    return 'ok'\n", encoding="utf-8")
        (self.root_path / "README.md").write_text("# Projeto de exemplo\n\nUsa app.py como entrada.\n", encoding="utf-8")
        captured = {}

        class SummaryContextClient:
            def __init__(self): self.calls = []
            def chat(self, messages, system_prompt=None, response_format=None):
                self.calls.append((messages, system_prompt, response_format))
                return "app.py contém função main; projeto é uma aplicação Python mínima."

        class PrincipalClient:
            def chat_stream(self, messages, system_prompt=None):
                captured["system_prompt"] = system_prompt
                yield "HTML criado"

        context = SummaryContextClient()
        processor = TurnProcessor(
            root_dir=self.tmp_dir.name, context_client=context, execution_client=PrincipalClient(), enable_context_summary=True,
        )
        from kitt.core.turn_command import TurnCommand
        list(processor.run_turn(TurnCommand("conv-context", "Crie um HTML explicando este projeto.")))
        self.assertEqual(len(context.calls), 1)
        self.assertIn("## Context v1", captured["system_prompt"])
        self.assertIn("return 'ok'", captured["system_prompt"])
        self.assertIn("Projeto de exemplo", captured["system_prompt"])
        self.assertNotIn("python_compute", captured["system_prompt"])
        self.assertFalse((self.root_path / ".kitt" / "context" / "latest.md").exists())

    def test_kitt_mention_enables_agent_identity_prompt(self):
        captured = {}
        class AgentClient:
            def chat_stream(self, messages, system_prompt=None):
                captured["system_prompt"] = system_prompt
                yield "K.I.T.T. response"

        self.processor.execution_client = AgentClient()
        from kitt.core.turn_command import TurnCommand
        list(self.processor.run_turn(TurnCommand("conv-kitt", "K.I.T.T., what can you do?")))
        self.assertIn("You are K.I.T.T.", captured["system_prompt"])

    def test_lfm_thinking_is_not_sent_to_ui(self):
        class LfmClient:
            profile = type("Profile", (), {"model": "lfm2.5-local"})()
            def chat_stream(self, *args, **kwargs):
                yield "<think>hidden reasoning</think>Final answer"

        events = list(self.processor._stream_execution_response(LfmClient(), [], ""))
        self.assertEqual(events[0][1].delta, "Final answer")

    def test_incomplete_lfm_thinking_is_not_used_as_context(self):
        self.assertEqual(self.processor._without_thinking("<think>unfinished"), "")
        self.assertEqual(self.processor._without_thinking("reasoning</think>final"), "final")

    def test_incomplete_lfm_thinking_shows_user_feedback(self):
        class LfmClient:
            profile = type("Profile", (), {"model": "lfm2.5-local"})()

            def chat_stream(self, *args, **kwargs):
                yield "<think>unfinished reasoning"

        events = list(self.processor._stream_execution_response(LfmClient(), [], ""))
        self.assertIn("Não recebi uma resposta final", events[0][1].delta)

    def test_incomplete_lfm_thinking_uses_final_answer_marker(self):
        class LfmClient:
            profile = type("Profile", (), {"model": "lfm2.5-local"})()

            def chat_stream(self, *args, **kwargs):
                yield "<think>reasoning without closing tag\nResposta final: resposta visível"

        events = list(self.processor._stream_execution_response(LfmClient(), [], ""))
        self.assertEqual(events[0][1].delta, "resposta visível")

    def test_context_summary_fallback_is_not_persisted(self):
        class UnavailableContext:
            def chat(self, *args, **kwargs): raise RuntimeError("unavailable")

        processor = TurnProcessor(root_dir=self.tmp_dir.name, enable_context_summary=True)
        summary = processor._summarize_project_context(UnavailableContext(), "Explain project", "stable structural context")
        self.assertEqual(summary, "stable structural context")
        self.assertFalse((self.root_path / ".kitt" / "context" / "latest.md").exists())

if __name__ == '__main__':
    unittest.main()
