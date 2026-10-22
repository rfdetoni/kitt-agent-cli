from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from kitt.core.runtime_config import RuntimeConfig
from kitt.core.turn_processor import TurnProcessor
from kitt.core.turn_tool_loop import TurnToolLoopMixin
from kitt.core.turn_finalization import TurnFinalizationMixin
from kitt.core.turn_context import TurnContextMixin
from kitt.core.turn_model import TurnModelMixin
from kitt.tools.registry import ToolRegistry
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import TurnBlocked, TurnCompleted, TurnFailed, ToolCompleted
from kitt.domain.entities import ModelProfile


class TurnProcessorDecompositionTests(unittest.TestCase):
    def test_unwritable_execution_journal_blocks_before_tool_execution(self):
        class Client:
            profile = ModelProfile(backend="freellmapi", protocol="openai-compatible",
                                   model="auto", context_window=32768)

            def chat_stream(self, messages, **kwargs):
                yield '<kitt-tool>{"name":"kitt_runtime","arguments":{"operation":"repo.create_directory","arguments":{"path":"mbs-wine"}}}</kitt-tool>'

        class UnwritableLedger:
            def reserve_tool_execution(self, *args, **kwargs):
                raise PermissionError("permission denied: .kitt/history.db")

        with tempfile.TemporaryDirectory() as root:
            processor = TurnProcessor(
                root_dir=root, execution_client=Client(),
                config=RuntimeConfig(history_enabled=False, persistence_enabled=False),
            )
            processor.event_ledger = UnwritableLedger()
            try:
                events = list(processor.run_turn(TurnCommand(
                    "conv", "Create mbs-wine directory.", no_history=True,
                )))
                self.assertTrue(any(isinstance(e, TurnBlocked) and
                                    "journal is unavailable" in e.reason for e in events))
                self.assertFalse((Path(root) / "mbs-wine").exists())
            finally:
                processor.close()
                processor.registry.close()

    def test_native_call_repair_keeps_prior_host_receipts(self):
        from kitt.llm.domain import ProviderRecoverableError

        class Client:
            profile = ModelProfile(backend="freellmapi", protocol="openai-compatible", model="auto", context_window=32768)
            calls = 0

            def chat_stream(self, messages, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    yield '<kitt-tool>{"id":"read_1","name":"kitt_runtime","arguments":{"operation":"repo.read","arguments":{"path":"report.md"}}}</kitt-tool>'
                elif self.calls == 2:
                    raise ProviderRecoverableError("Return exactly one tool call", code="native_tool_call_invalid")
                else:
                    assert any("HOST_STATUS: success" in str(m.get("content")) and "READ_OK" in str(m.get("content")) for m in messages), [m.get("content") for m in messages if m.get("role") == "user"][-2:]
                    yield "READ_OK"

        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "report.md").write_text("READ_OK")
            client = Client()
            processor = TurnProcessor(root_dir=root, execution_client=client,
                config=RuntimeConfig(history_enabled=False, persistence_enabled=False))
            try:
                events = list(processor.run_turn(TurnCommand("conv", "Read report.md and return its contents.", no_history=True)))
                self.assertIsInstance(events[-1], TurnCompleted)
                self.assertEqual(events[-1].response, "READ_OK")
                self.assertEqual(sum(isinstance(e, ToolCompleted) for e in events), 1)
                self.assertEqual(client.calls, 3)
                with patch.object(client, "chat_stream", side_effect=ProviderRecoverableError(
                    "Return exactly one tool call", code="native_tool_call_invalid",
                )) as stream:
                    failed = list(processor.run_turn(TurnCommand("conv", "Read report.md.", no_history=True)))
                    self.assertIsInstance(failed[-1], TurnFailed)
                    self.assertEqual(stream.call_count, 3)
                    self.assertFalse(any(isinstance(e, ToolCompleted) for e in failed))
            finally:
                processor.close()
                processor.registry.close()

    def test_read_only_structured_response_never_becomes_a_file_edit(self):
        response = '{"items":[{"prompt":"Keep <pending> status in report.md"}]}'

        class Client:
            profile = ModelProfile(backend="freellmapi", protocol="openai-compatible", model="auto", context_window=32768)

            def chat_stream(self, messages, **kwargs):
                yield response

        for mode in ("plan", "ask"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                path = Path(root) / "report.md"
                path.write_text("unchanged")
                processor = TurnProcessor(
                    root_dir=root, execution_client=Client(),
                    config=RuntimeConfig(history_enabled=False, persistence_enabled=False),
                )
                try:
                    events = list(processor.run_turn(TurnCommand(
                        "conv", "Explain the proposed change to report.md without editing it.",
                        mode=mode, explicit_files={"report.md"}, no_history=True,
                    )))
                    self.assertIsInstance(events[-1], TurnCompleted)
                    self.assertEqual(events[-1].response, response)
                    self.assertIsNone(events[-1].edit_result)
                    self.assertEqual(path.read_text(), "unchanged")
                finally:
                    processor.close()
                    processor.registry.close()

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
