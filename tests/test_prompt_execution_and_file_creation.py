import tempfile
import unittest
from pathlib import Path

from kitt.core.runtime import KittRuntime
from kitt.core.turn_command import TurnCommand
from kitt.security.capabilities import (
    CAP_PROCESS_RUN,
    CAP_REPO_READ,
    CAP_REPO_SEARCH,
    CAP_REPO_WRITE,
    capabilities_for_tools,
)
from kitt.tools.surface_selector import ToolSurfaceSelector


class TestPromptExecutionAndFileCreation(unittest.TestCase):
    def test_file_creation_tool_execution_with_autonomy(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            with KittRuntime.build(root_dir=tmp_dir) as runtime:
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
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            with KittRuntime.build(root_dir=tmp_dir) as runtime:
                runtime.approval.remember("write_file", "**", "allow", scope="workspace")

                res = runtime.registry.execute_tool(
                    "write_file",
                    {"path": "com_regra.py", "content": "hello"}
                )
                self.assertTrue(res.success)
                self.assertTrue((Path(tmp_dir) / "com_regra.py").exists())

    def test_safe_runtime_surface_keeps_fallback_write_capabilities_internal(self):
        surface = ToolSurfaceSelector._safe_runtime_surface()
        self.assertEqual(list(surface), ["kitt_runtime"])
        self.assertIn("write_file", surface)
        self.assertIn("apply_patch", surface)
        self.assertNotIn("run_command", surface)

    def test_compact_runtime_grants_only_repository_capability_family(self):
        capabilities = capabilities_for_tools(["kitt_runtime"])

        self.assertIn(CAP_REPO_READ, capabilities)
        self.assertIn(CAP_REPO_SEARCH, capabilities)
        self.assertIn(CAP_REPO_WRITE, capabilities)
        self.assertNotIn(CAP_PROCESS_RUN, capabilities)

    def test_compact_runtime_write_capability_is_available_for_code_turns(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            with KittRuntime.build(root_dir=tmp_dir) as runtime:
                code_context = runtime.processor._security_context_for_turn(
                    TurnCommand(conversation_id="code-turn", prompt="continue a implementação", mode="code"),
                    ["kitt_runtime"],
                )
                ask_context = runtime.processor._security_context_for_turn(
                    TurnCommand(conversation_id="ask-turn", prompt="explique o projeto", mode="ask"),
                    ["kitt_runtime"],
                )

                self.assertTrue(code_context.has_capability(CAP_REPO_WRITE))
                self.assertFalse(ask_context.has_capability(CAP_REPO_WRITE))

    def test_turn_processor_enables_file_writing_tools_for_general_prompts(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            with KittRuntime.build(root_dir=tmp_dir) as runtime:
                cmd = TurnCommand(conversation_id="c1", prompt="crie um arquivo index.html")

                events = list(runtime.processor.run_turn(cmd))
                # Verify plan has write_file and apply_patch enabled
                plan = runtime.processor.session_state.last_plan
                self.assertIsNotNone(plan)
                self.assertIn("write_file", plan.enabled_tools)
                self.assertIn("apply_patch", plan.enabled_tools)


if __name__ == "__main__":
    unittest.main()
