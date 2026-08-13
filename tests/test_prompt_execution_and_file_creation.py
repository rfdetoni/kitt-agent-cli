import tempfile
import unittest
from pathlib import Path
from kitt.core.runtime import KittRuntime
from kitt.core.turn_command import TurnCommand

class TestPromptExecutionAndFileCreation(unittest.TestCase):
    def test_file_creation_tool_execution_with_autonomy(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = KittRuntime.build(root_dir=tmp_dir)
            runtime.autonomy_store.set_preset("files_free")
            runtime.processor.registry.policy.autonomy = runtime.autonomy_store.get()

            # Test write_file tool directly via registry in files_free mode
            res = runtime.registry.execute_tool(
                "write_file",
                {"path": "teste_criado.py", "content": "print('criado via prompt')"}
            )
            self.assertTrue(res.success)
            self.assertIn("teste_criado.py", res.output)

            created_path = Path(tmp_dir) / "teste_criado.py"
            self.assertTrue(created_path.exists())
            self.assertEqual(created_path.read_text(encoding="utf-8"), "print('criado via prompt')")

    def test_file_creation_tool_execution_with_remembered_rule(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = KittRuntime.build(root_dir=tmp_dir)
            runtime.approval.remember("write_file", "**", "allow", scope="session")

            res = runtime.registry.execute_tool(
                "write_file",
                {"path": "com_regra.py", "content": "hello"}
            )
            self.assertTrue(res.success)
            self.assertTrue((Path(tmp_dir) / "com_regra.py").exists())

    def test_turn_processor_enables_file_writing_tools_for_general_prompts(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = KittRuntime.build(root_dir=tmp_dir)
            cmd = TurnCommand(conversation_id="c1", prompt="crie um arquivo index.html")

            events = list(runtime.processor.run_turn(cmd))
            # Verify plan has write_file and apply_patch enabled
            plan = runtime.processor.session_state.last_plan
            self.assertIsNotNone(plan)
            self.assertIn("write_file", plan.enabled_tools)
            self.assertIn("apply_patch", plan.enabled_tools)

if __name__ == "__main__":
    unittest.main()
