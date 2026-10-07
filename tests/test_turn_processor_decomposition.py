from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace

from kitt.core.runtime_config import RuntimeConfig
from kitt.core.turn_processor import TurnProcessor
from kitt.core.turn_tool_loop import TurnToolLoopMixin
from kitt.core.turn_finalization import TurnFinalizationMixin
from kitt.core.turn_context import TurnContextMixin
from kitt.core.turn_model import TurnModelMixin
from kitt.tools.registry import ToolRegistry


class TurnProcessorDecompositionTests(unittest.TestCase):
    def test_tool_loop_is_owned_by_dedicated_phase_mixin(self):
        self.assertTrue(issubclass(TurnProcessor, TurnToolLoopMixin))
        self.assertNotIn("_execute_tool_loop", TurnProcessor.__dict__)
        self.assertIs(
            TurnProcessor._execute_tool_loop,
            TurnToolLoopMixin._execute_tool_loop,
        )

    def test_context_phase_is_owned_by_dedicated_mixin(self):
        self.assertTrue(issubclass(TurnProcessor, TurnContextMixin))
        for method_name in (
            "_run_semantic_filter",
            "_build_context",
            "_build_system_prompt",
        ):
            self.assertNotIn(method_name, TurnProcessor.__dict__)
            self.assertIs(
                getattr(TurnProcessor, method_name),
                getattr(TurnContextMixin, method_name),
            )

    def test_model_phase_is_owned_by_dedicated_mixin(self):
        self.assertTrue(issubclass(TurnProcessor, TurnModelMixin))
        for method_name in (
            "_resolve_execution_profile",
            "_stream_execution_response",
            "_without_thinking",
        ):
            self.assertNotIn(method_name, TurnProcessor.__dict__)
            self.assertIs(
                getattr(TurnProcessor, method_name),
                getattr(TurnModelMixin, method_name),
            )

    def test_model_cache_usage_accepts_common_provider_shapes(self):
        self.assertEqual(
            TurnModelMixin._cached_prompt_tokens(
                {"prompt_tokens_details": {"cached_tokens": 321}}
            ),
            321,
        )
        self.assertEqual(
            TurnModelMixin._cached_prompt_tokens(
                {"cache_read_input_tokens": 77}
            ),
            77,
        )
        self.assertIsNone(TurnModelMixin._cached_prompt_tokens({}))

    def test_finalization_is_owned_by_dedicated_phase_mixin(self):
        self.assertTrue(issubclass(TurnProcessor, TurnFinalizationMixin))
        self.assertNotIn("_finalize_turn", TurnProcessor.__dict__)
        self.assertIs(
            TurnProcessor._finalize_turn,
            TurnFinalizationMixin._finalize_turn,
        )

    def test_agent_engineering_keeps_native_entrypoints_and_returns_role_denial(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            registry = ToolRegistry(root_dir=tmp_dir)
            processor = TurnProcessor(
                root_dir=tmp_dir,
                registry=registry,
                config=RuntimeConfig(
                    history_enabled=False,
                    persistence_enabled=False,
                ),
            )
            try:
                before = (
                    processor.run_turn.__func__,
                    processor.continue_turn.__func__,
                    processor.resume_turn.__func__,
                    registry.execute_tool.__func__,
                )

                registry.attach_processor(processor)

                after = (
                    processor.run_turn.__func__,
                    processor.continue_turn.__func__,
                    processor.resume_turn.__func__,
                    registry.execute_tool.__func__,
                )
                self.assertEqual(after, before)
                self.assertIs(processor.run_turn.__func__, TurnProcessor.run_turn)
                self.assertIs(registry.execute_tool.__func__, ToolRegistry.execute_tool)

                processor._agent_role_policies = {
                    "turn-role-denied": SimpleNamespace(
                        role="VERIFY",
                        allows_tool=lambda *_args: False,
                    )
                }
                result = registry.execute_tool(
                    "read_file",
                    {"path": "ignored.txt"},
                    turn_id="turn-role-denied",
                    conversation_id="conv-role-denied",
                    workspace_id="local",
                    enabled_tools=["read_file"],
                )

                self.assertFalse(result.success)
                self.assertTrue(result.metadata["role_policy_denied"])
                self.assertIn("does not allow read_file", result.error)
            finally:
                processor.close()
                registry.close()


if __name__ == "__main__":
    unittest.main()
