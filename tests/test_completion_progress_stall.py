import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kitt.core.completion_guard import (
    _ProgressAwareExecutionLedger,
    install_completion_guard,
)
from kitt.core.execution_request import ExecutionRequest
from kitt.core.turn_events import ToolCompleted, ToolStarted, TurnFailed


class _Registry:
    def __init__(self, root: Path):
        self.root_path = root


class CompletionProgressStallTests(unittest.TestCase):
    def test_alternating_a_b_loop_is_detected(self):
        ledger = _ProgressAwareExecutionLedger()
        calls = [
            ("a-1", {"operation": "repo.list", "arguments": {"path": "."}}),
            ("b-1", {"operation": "repo.search", "arguments": {"pattern": "Foo"}}),
            ("a-2", {"operation": "repo.list", "arguments": {"path": "."}}),
        ]
        for call_id, args in calls:
            started = ToolStarted(
                tool_name="kitt_runtime",
                args=args,
                call_id=call_id,
            )
            self.assertIsNone(ledger.start(started))
            ledger.complete(
                ToolCompleted(
                    tool_name="kitt_runtime",
                    success=True,
                    output="same",
                    call_id=call_id,
                )
            )

        stall = ledger.start(
            ToolStarted(
                tool_name="kitt_runtime",
                args={
                    "operation": "repo.search",
                    "arguments": {"pattern": "Foo"},
                },
                call_id="b-2",
            )
        )

        self.assertIsNotNone(stall)
        self.assertIn("A/B/A/B", stall)

    def test_prose_only_mutation_attempt_gets_nudge_then_hard_stop(self):
        class Processor:
            def __init__(self):
                self.calls = 0
                self.session_state = SimpleNamespace(
                    last_task=SimpleNamespace(
                        intent="IMPLEMENT",
                        actions=["edit"],
                    )
                )

            def _execute_tool_loop(
                self,
                cmd,
                request,
                *args,
                agent_route=None,
                **kwargs,
            ):
                self.calls += 1
                yield None, "I implemented it in prose.", list(request.messages)

        with tempfile.TemporaryDirectory() as tmp:
            processor = Processor()
            install_completion_guard(processor, _Registry(Path(tmp)))
            cmd = SimpleNamespace(
                prompt="crie o projeto e implemente o backend",
                mode="auto",
                turn_id="turn-prose",
            )
            request = ExecutionRequest(
                system_prompt="test",
                messages=[{"role": "user", "content": cmd.prompt}],
                enabled_tools=["kitt_runtime"],
            )

            events = list(
                processor._execute_tool_loop(
                    cmd,
                    request,
                    None,
                    None,
                    "local",
                    None,
                )
            )
            failures = [
                event
                for event, _, _ in events
                if isinstance(event, TurnFailed)
            ]

            self.assertEqual(processor.calls, 2)
            self.assertEqual(len(failures), 1)
            self.assertIn("prose-only", failures[0].error)
            self.assertIn("requested mutation was never attempted", failures[0].error)

    def test_three_identical_repo_lists_fail_before_runaway_loop(self):
        class Processor:
            def __init__(self):
                self.session_state = SimpleNamespace(
                    last_task=SimpleNamespace(intent="IMPLEMENT", actions=["analyze", "edit"])
                )

            def _execute_tool_loop(self, cmd, request, *args, agent_route=None, **kwargs):
                for index in range(3):
                    call_id = f"list-{index}"
                    tool_args = {"operation": "repo.list", "arguments": {"path": "."}}
                    yield ToolStarted(tool_name="kitt_runtime", args=tool_args, call_id=call_id), None, None
                    yield ToolCompleted(tool_name="kitt_runtime", success=True, output="[]", call_id=call_id), None, None
                yield None, "Ainda analisando.", list(request.messages)

        with tempfile.TemporaryDirectory() as tmp:
            processor = Processor()
            install_completion_guard(processor, _Registry(Path(tmp)))
            cmd = SimpleNamespace(prompt="crie o projeto e implemente o backend", mode="auto")
            request = ExecutionRequest(
                system_prompt="test",
                messages=[{"role": "user", "content": cmd.prompt}],
                enabled_tools=["kitt_runtime"],
            )
            events = list(processor._execute_tool_loop(cmd, request, None, None, "local", None))
            failures = [event for event, _, _ in events if isinstance(event, TurnFailed)]
            self.assertEqual(len(failures), 1)
            self.assertIn("Execution stalled", failures[0].error)
            self.assertIn("identical exploration repeated", failures[0].error)


if __name__ == "__main__":
    unittest.main()
